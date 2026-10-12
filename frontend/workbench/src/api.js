export async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers
    }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.error || `请求失败 (${response.status})`);
    error.status = response.status;
    error.data = data;
    error.reasonCodes = data.reason_codes || [];
    throw error;
  }
  return data;
}

export function post(path, data) {
  return api(path, { method: 'POST', body: JSON.stringify(data) });
}
