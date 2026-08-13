use std::{fs, net::TcpListener, path::PathBuf, sync::Mutex};

use tauri::{
    path::BaseDirectory, webview::WebviewWindowBuilder, AppHandle, Manager, RunEvent, WebviewUrl,
};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

#[cfg(windows)]
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::{
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
            SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        },
        Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE},
    },
};

struct BackendProcess {
    child: Option<CommandChild>,
    #[cfg(windows)]
    job: Option<WindowsJob>,
}

#[cfg(windows)]
struct WindowsJob(usize);

#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0 as HANDLE);
        }
    }
}

#[cfg(windows)]
fn assign_kill_on_close_job(process_id: u32) -> Result<WindowsJob, String> {
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return Err(format!(
                "failed to create backend process job: {}",
                std::io::Error::last_os_error()
            ));
        }

        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const _,
            std::mem::size_of_val(&limits) as u32,
        ) == 0
        {
            let error = std::io::Error::last_os_error();
            CloseHandle(job);
            return Err(format!("failed to configure backend process job: {error}"));
        }

        let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, process_id);
        if process.is_null() {
            let error = std::io::Error::last_os_error();
            CloseHandle(job);
            return Err(format!("failed to open backend process: {error}"));
        }
        let assigned = AssignProcessToJobObject(job, process);
        CloseHandle(process);
        if assigned == 0 {
            let error = std::io::Error::last_os_error();
            CloseHandle(job);
            return Err(format!("failed to assign backend process job: {error}"));
        }

        Ok(WindowsJob(job as usize))
    }
}

fn available_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("failed to reserve backend port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("failed to read backend port: {error}"))
}

fn desktop_data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let path = match std::env::var_os("AGENTHUB_DESKTOP_DATA_DIR") {
        Some(value) => PathBuf::from(value),
        None => app
            .path()
            .resolve("AgentHub", BaseDirectory::AppLocalData)
            .map_err(|error| format!("failed to resolve desktop data directory: {error}"))?,
    };
    fs::create_dir_all(&path)
        .map_err(|error| format!("failed to create desktop data directory: {error}"))?;
    Ok(path)
}

fn stop_backend(app: &AppHandle) {
    let state = app.state::<Mutex<BackendProcess>>();
    let child;
    #[cfg(windows)]
    let job;
    match state.lock() {
        Ok(mut process) => {
            child = process.child.take();
            #[cfg(windows)]
            {
                job = process.job.take();
            }
        }
        Err(_) => return,
    }
    if let Some(child) = child {
        let _ = child.kill();
    }
    #[cfg(windows)]
    drop(job);
}

fn start_backend(app: &AppHandle, port: u16, data_dir: &PathBuf) -> Result<(), String> {
    let arguments = vec![
        "--data-dir".to_string(),
        data_dir.to_string_lossy().into_owned(),
        "--port".to_string(),
        port.to_string(),
    ];
    let sidecar = app
        .shell()
        .sidecar("agenthub-backend")
        .map_err(|error| format!("failed to prepare backend sidecar: {error}"))?
        .args(arguments);
    let (mut events, child) = sidecar
        .spawn()
        .map_err(|error| format!("failed to start backend sidecar: {error}"))?;

    #[cfg(windows)]
    let job = match assign_kill_on_close_job(child.pid()) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            return Err(error);
        }
    };

    let state = app.state::<Mutex<BackendProcess>>();
    let mut process = state
        .lock()
        .map_err(|_| "backend process state is unavailable".to_string())?;
    process.child.replace(child);
    #[cfg(windows)]
    process.job.replace(job);

    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    println!("[agenthub-backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[agenthub-backend] {}", String::from_utf8_lossy(&line));
                }
                CommandEvent::Error(error) => {
                    eprintln!("[agenthub-backend] process error: {error}");
                }
                CommandEvent::Terminated(status) => {
                    eprintln!("[agenthub-backend] terminated: {status:?}");
                }
                _ => {}
            }
        }
    });
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .manage(Mutex::new(BackendProcess {
            child: None,
            #[cfg(windows)]
            job: None,
        }))
        .setup(|app| {
            let port = available_port()?;
            let data_dir = desktop_data_dir(app.handle())?;
            start_backend(app.handle(), port, &data_dir)?;

            let url = format!("index.html?desktopApiPort={port}");
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App(url.into()))
                .title("AgentHub")
                .inner_size(1280.0, 860.0)
                .min_inner_size(980.0, 680.0)
                .build()
                .map_err(|error| format!("failed to create main window: {error}"))?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build AgentHub desktop application");

    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_backend(handle);
        }
    });
}
