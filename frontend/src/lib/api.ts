declare global {
  interface Window {
    pywebview?: { api: Record<string, (...args: any[]) => Promise<any>> };
  }
}
export async function api(name: string, ...args: any[]) {
  if (typeof window.pywebview?.api?.[name] !== "function") throw new Error("正在等待桌面连接，请稍后重试");
  const result = await window.pywebview.api[name](...args);
  return result;
}
export function backendReady() {
  return ["get_state", "workbench_library", "agent_state"].every(name => typeof window.pywebview?.api?.[name] === "function");
}
