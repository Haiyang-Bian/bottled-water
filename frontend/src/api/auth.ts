import { get, post, patch } from "./client";
import type { User } from "@/types";

export async function login(name: string, password: string): Promise<User> {
  const result = await post<{ access_token: string; user: User }>("/auth/login", {
    username: name,
    email: name,
    password,
  });
  window.localStorage.setItem("agenthub_token", result.access_token);
  return result.user;
}

export async function register(payload: {
  email: string;
  username: string;
  password: string;
  display_name?: string;
}): Promise<User> {
  const result = await post<{ access_token: string; user: User }>("/auth/signup", payload);
  window.localStorage.setItem("agenthub_token", result.access_token);
  return result.user;
}

export async function updateProfile(payload: {
  display_name?: string;
  name?: string;
  avatar_url?: string;
  signature?: string;
  settings?: Record<string, unknown>;
}): Promise<User> {
  return await patch<User>("/auth/me", payload);
}

export async function changePassword(payload: {
  current_password: string;
  new_password: string;
}): Promise<{ changed: boolean }> {
  return await post<{ changed: boolean }>("/auth/password", payload);
}

export async function me(): Promise<User> {
  return await get<User>("/auth/me");
}

export async function logout(): Promise<void> {
  try {
    await post<{ ok: boolean }>("/auth/logout", {});
  } finally {
    window.localStorage.removeItem("agenthub_token");
  }
}
