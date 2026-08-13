from __future__ import annotations

from html import escape
from typing import Any

from common.artifact_heuristics import HTML_ARTIFACT_TOOLS as _HTML_ARTIFACT_TOOLS


HTML_ARTIFACT_TOOLS = _HTML_ARTIFACT_TOOLS

def html_artifact_arguments(prompt: str) -> dict[str, str]:
    title = _title_from_prompt(prompt)
    return {
        "title": title,
        "body": prompt,
    }


def normalize_html_artifact_arguments(prompt: str, arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(arguments)
    title = str(normalized.get("title") or _title_from_prompt(prompt))
    html = str(normalized.get("html") or "")
    issues = html_quality_issues(prompt, html)
    if issues:
        normalized["quality_report"] = {
            "fallback_applied": False,
            "issues": issues,
            "message": "HTML 参数质量不足，已保留 Agent 原始输出；不会自动套用兜底模板。",
        }
    normalized.setdefault("title", title)
    normalized.setdefault("body", prompt)
    return normalized


def build_html_fallback(prompt: str, *, title: str | None = None) -> str:
    raise ValueError(
        "HTML artifact fallback templates are disabled. "
        "The Agent must provide real HTML/CSS/JS content generated for the current task."
    )


def html_quality_issues(prompt: str, html: str) -> list[str]:
    content = html.strip()
    lower = content.lower()
    issues: list[str] = []
    if len(content) < 800:
        issues.append("html_too_short")
    if "<!doctype html" not in lower:
        issues.append("missing_doctype")
    if "<html" not in lower or "<head" not in lower or "<body" not in lower:
        issues.append("missing_document_structure")
    if _requires_interaction(prompt):
        if "<script" not in lower:
            issues.append("interactive_missing_script")
        if not any(token in lower for token in ("<button", "<input", "<select", "<textarea")):
            issues.append("interactive_missing_controls")
    if _template_kind(prompt) == "calculator":
        if "function calculate" not in lower:
            issues.append("calculator_missing_calculate_function")
        if "appendvalue" not in lower or "display" not in lower:
            issues.append("calculator_missing_input_logic")
    return issues


def _template_kind(prompt: str) -> str:
    lower = prompt.lower()
    if any(token in lower for token in ("计算器", "calculator", "calc")):
        return "calculator"
    if any(token in lower for token in ("登录", "登陆", "login", "sign in")):
        return "login"
    if any(token in lower for token in ("表单", "报名", "问卷", "form")):
        return "form"
    if any(token in lower for token in ("看板", "仪表盘", "dashboard", "kanban")):
        return "dashboard"
    return "default"


def _requires_interaction(prompt: str) -> bool:
    lower = prompt.lower()
    return any(
        token in lower
        for token in (
            "计算器",
            "calculator",
            "表单",
            "登录",
            "登陆",
            "看板",
            "按钮",
            "输入",
            "交互",
            "web app",
            "网页",
            "页面",
            "html",
        )
    )


def _title_from_prompt(prompt: str) -> str:
    first_line = (prompt or "").strip().splitlines()[0] if (prompt or "").strip() else ""
    return first_line[:60] or "AgentHub HTML 产物"


def _base_html(title: str, body: str, script: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --panel: #ffffff;
      --text: #172033;
      --muted: #65758b;
      --primary: #1677ff;
      --primary-dark: #0f5dcc;
      --line: #d9e2ef;
      --success: #16a34a;
      --warning: #f59e0b;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      background: radial-gradient(circle at top left, #e8f1ff 0, transparent 32rem), var(--bg);
      color: var(--text);
    }}
    main {{
      width: min(1080px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 42px 0;
    }}
    .hero {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: flex-end;
      margin-bottom: 24px;
    }}
    h1 {{ margin: 0; font-size: clamp(28px, 4vw, 48px); line-height: 1.1; }}
    p {{ color: var(--muted); line-height: 1.7; }}
    .card {{
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 18px 45px rgba(26, 50, 89, 0.12);
      padding: 24px;
    }}
    button {{
      border: 0;
      border-radius: 12px;
      background: var(--primary);
      color: white;
      padding: 12px 16px;
      font-weight: 700;
      cursor: pointer;
      transition: transform 0.15s ease, background 0.15s ease;
    }}
    button:hover {{ background: var(--primary-dark); transform: translateY(-1px); }}
    input, textarea, select {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 14px;
      font: inherit;
      outline: none;
      background: #fff;
    }}
    input:focus, textarea:focus, select:focus {{ border-color: var(--primary); box-shadow: 0 0 0 3px #1677ff22; }}
    .grid {{ display: grid; gap: 18px; }}
    .two {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .muted {{ color: var(--muted); }}
    .pill {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 999px; background: #eef5ff; color: var(--primary-dark); font-weight: 700; }}
    @media (max-width: 760px) {{
      .hero {{ display: block; }}
      .two {{ grid-template-columns: 1fr; }}
      main {{ width: min(100vw - 20px, 1080px); padding: 24px 0; }}
    }}
  </style>
</head>
<body>
{body}
<script>
{script}
</script>
</body>
</html>"""


def _calculator_template(title: str) -> str:
    body = f"""
<main>
  <section class="hero">
    <div>
      <span class="pill">可运行计算器</span>
      <h1>{escape(title)}</h1>
      <p>支持加、减、乘、除、小数和括号运算。点击按钮即可输入，按等号得到结果。</p>
    </div>
  </section>
  <section class="card" aria-label="计算器">
    <input id="display" aria-label="计算表达式" readonly placeholder="0" />
    <div class="calculator-grid" id="calculatorButtons">
      <button type="button" class="danger" data-action="clear">C</button>
      <button type="button" data-action="backspace">⌫</button>
      <button type="button" data-value="(">(</button>
      <button type="button" data-value=")">)</button>
      <button type="button" data-value="7">7</button>
      <button type="button" data-value="8">8</button>
      <button type="button" data-value="9">9</button>
      <button type="button" class="operator" data-value="÷">÷</button>
      <button type="button" data-value="4">4</button>
      <button type="button" data-value="5">5</button>
      <button type="button" data-value="6">6</button>
      <button type="button" class="operator" data-value="×">×</button>
      <button type="button" data-value="1">1</button>
      <button type="button" data-value="2">2</button>
      <button type="button" data-value="3">3</button>
      <button type="button" class="operator" data-value="-">-</button>
      <button type="button" data-value="0">0</button>
      <button type="button" data-value=".">.</button>
      <button type="button" class="equal" data-action="calculate">=</button>
      <button type="button" class="operator" data-value="+">+</button>
    </div>
    <p id="message" class="muted">提示：只能计算安全的数字表达式。</p>
  </section>
</main>
<style>
  #display {{ height: 64px; margin-bottom: 18px; text-align: right; font-size: 30px; font-weight: 800; }}
  .calculator-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
  .calculator-grid button {{ min-height: 54px; font-size: 18px; }}
  .calculator-grid button.operator {{ background: #0f766e; }}
  .calculator-grid button.danger {{ background: #dc2626; }}
  .calculator-grid button.equal {{ grid-column: span 2; background: #16a34a; }}
</style>"""
    script = """
const display = document.getElementById('display');
const message = document.getElementById('message');
const grid = document.getElementById('calculatorButtons');

function appendValue(value) {
  const normalized = value === '×' ? '*' : value === '÷' ? '/' : value;
  display.value = display.value === '0' ? normalized : display.value + normalized;
  message.textContent = '正在输入表达式';
}

function clearDisplay() {
  display.value = '';
  message.textContent = '已清空';
}

function backspace() {
  display.value = display.value.slice(0, -1);
}

function calculate() {
  const expression = display.value.trim();
  if (!expression) {
    message.textContent = '请先输入表达式';
    return;
  }
  if (!/^[0-9+\\-*/().\\s]+$/.test(expression)) {
    message.textContent = '表达式包含不支持的字符';
    return;
  }
  try {
    const value = Function(`"use strict"; return (${expression})`)();
    if (!Number.isFinite(value)) throw new Error('invalid result');
    display.value = String(Number(value.toFixed(10)));
    message.textContent = '计算完成';
  } catch (error) {
    message.textContent = '表达式有误，请检查后重试';
  }
}

grid.querySelectorAll('button').forEach((button) => {
  button.addEventListener('click', () => {
    if (button.dataset.action === 'clear') return clearDisplay();
    if (button.dataset.action === 'backspace') return backspace();
    if (button.dataset.action === 'calculate') return calculate();
    return appendValue(button.dataset.value || '');
  });
});
"""
    return _base_html(title, body, script)


def _form_template(title: str) -> str:
    body = f"""
<main>
  <section class="hero">
    <div>
      <span class="pill">表单演示</span>
      <h1>{escape(title)}</h1>
      <p>包含字段校验、提交反馈和本地列表展示，可直接作为页面原型继续扩展。</p>
    </div>
  </section>
  <section class="grid two">
    <form class="card" id="demoForm">
      <label>姓名<input id="name" required placeholder="请输入姓名" /></label>
      <label>邮箱<input id="email" type="email" required placeholder="name@example.com" /></label>
      <label>需求<textarea id="note" rows="4" placeholder="写下你的需求"></textarea></label>
      <button type="submit">提交表单</button>
    </form>
    <div class="card">
      <h2>提交记录</h2>
      <div id="result" class="muted">暂无提交</div>
    </div>
  </section>
</main>"""
    script = """
const form = document.getElementById('demoForm');
const result = document.getElementById('result');
const records = [];
form.addEventListener('submit', (event) => {
  event.preventDefault();
  const data = {
    name: document.getElementById('name').value.trim(),
    email: document.getElementById('email').value.trim(),
    note: document.getElementById('note').value.trim()
  };
  if (!data.name || !data.email) {
    result.textContent = '请填写必填字段。';
    return;
  }
  records.unshift(data);
  result.innerHTML = records.map(item => `<article><strong>${item.name}</strong><p>${item.email}</p><p>${item.note || '无备注'}</p></article>`).join('');
  form.reset();
});
"""
    return _base_html(title, body, script)


def _dashboard_template(title: str) -> str:
    body = f"""
<main>
  <section class="hero">
    <div>
      <span class="pill">运营看板</span>
      <h1>{escape(title)}</h1>
      <p>包含指标卡片、进度条和任务列表，适合演示业务看板或项目状态页。</p>
    </div>
    <button id="refreshBtn" type="button">刷新数据</button>
  </section>
  <section class="grid two" id="metrics"></section>
  <section class="card" style="margin-top:18px">
    <h2>待办事项</h2>
    <div id="tasks"></div>
  </section>
</main>"""
    script = """
const metricNames = ['访问量', '转化率', '活跃用户', '满意度'];
const tasks = ['确认需求范围', '完善交互细节', '输出验收标准', '准备演示数据'];
function renderDashboard() {
  const metrics = document.getElementById('metrics');
  metrics.innerHTML = metricNames.map(name => {
    const value = Math.floor(40 + Math.random() * 58);
    return `<article class="card"><p class="muted">${name}</p><h2>${value}${name.includes('率') || name.includes('度') ? '%' : ''}</h2><progress value="${value}" max="100"></progress></article>`;
  }).join('');
  document.getElementById('tasks').innerHTML = tasks.map((task, index) => `<label style="display:block;margin:10px 0"><input type="checkbox" ${index < 2 ? 'checked' : ''}/> ${task}</label>`).join('');
}
document.getElementById('refreshBtn').addEventListener('click', renderDashboard);
renderDashboard();
"""
    return _base_html(title, body, script)


def _login_template(title: str) -> str:
    body = f"""
<main>
  <section class="grid two">
    <div class="card">
      <span class="pill">登录页</span>
      <h1>{escape(title)}</h1>
      <p>包含账号密码输入、基础校验、状态提示和记住登录选项。</p>
    </div>
    <form class="card" id="loginForm">
      <label>账号<input id="account" autocomplete="username" placeholder="demo@agenthub.local" required /></label>
      <label>密码<input id="password" type="password" autocomplete="current-password" placeholder="至少 6 位" required /></label>
      <label style="display:flex;gap:8px;align-items:center"><input id="remember" type="checkbox" style="width:auto" /> 记住登录状态</label>
      <button type="submit">登录</button>
      <p id="loginMessage" class="muted">请输入账号和密码。</p>
    </form>
  </section>
</main>"""
    script = """
document.getElementById('loginForm').addEventListener('submit', (event) => {
  event.preventDefault();
  const account = document.getElementById('account').value.trim();
  const password = document.getElementById('password').value;
  const message = document.getElementById('loginMessage');
  if (!account.includes('@')) {
    message.textContent = '请输入有效邮箱账号。';
    return;
  }
  if (password.length < 6) {
    message.textContent = '密码至少需要 6 位。';
    return;
  }
  message.textContent = `欢迎回来，${account}。这是一个前端登录交互演示。`;
});
"""
    return _base_html(title, body, script)


def _default_template(title: str, prompt: str) -> str:
    safe_prompt = escape(prompt or "这是一个可运行的 HTML 页面。")
    body = f"""
<main>
  <section class="hero">
    <div>
      <span class="pill">HTML 示例</span>
      <h1>{escape(title)}</h1>
      <p>{safe_prompt}</p>
    </div>
    <button id="themeBtn" type="button">切换强调色</button>
  </section>
  <section class="grid two">
    <article class="card">
      <h2>页面能力</h2>
      <ul>
        <li>完整 HTML/CSS/JS 结构</li>
        <li>响应式布局和按钮交互</li>
        <li>可直接预览、下载和继续编辑</li>
      </ul>
    </article>
    <article class="card">
      <h2>互动区域</h2>
      <input id="ideaInput" placeholder="输入一句页面文案" />
      <button id="addBtn" type="button" style="margin-top:12px">加入列表</button>
      <div id="ideas" class="muted" style="margin-top:14px">暂无内容</div>
    </article>
  </section>
</main>"""
    script = """
const colors = ['#1677ff', '#0f766e', '#7c3aed', '#ea580c'];
let colorIndex = 0;
document.getElementById('themeBtn').addEventListener('click', () => {
  colorIndex = (colorIndex + 1) % colors.length;
  document.documentElement.style.setProperty('--primary', colors[colorIndex]);
});
document.getElementById('addBtn').addEventListener('click', () => {
  const input = document.getElementById('ideaInput');
  const value = input.value.trim();
  if (!value) return;
  const ideas = document.getElementById('ideas');
  ideas.innerHTML = `<p>• ${value}</p>` + (ideas.textContent === '暂无内容' ? '' : ideas.innerHTML);
  input.value = '';
});
"""
    return _base_html(title, body, script)
