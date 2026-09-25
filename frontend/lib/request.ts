import axios, { AxiosError, AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios';

const request: AxiosInstance = axios.create({
  baseURL: '',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

request.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('auth_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    // 实例上默认写死了 application/json，发 FormData 时必须去掉，
    // 否则表单会被当成 JSON 发送，后端 Form(...) 解析不到字段而返回 422。
    // 删掉后 axios 会自动补上带 boundary 的 multipart/form-data。
    if (typeof FormData !== 'undefined' && config.data instanceof FormData) {
      delete config.headers['Content-Type'];
    }
    return config;
  },
  (error) => Promise.reject(error),
);

request.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token');
      try {
        window.dispatchEvent(new Event('auth:logout'));
      } catch {
        /* no-op (SSR) */
      }
    }
    // 后端（FastAPI）的错误信息在 response.data.detail 里。默认情况下 axios
    // 的 error.message 只是 "Request failed with status code 400" 这类通用提示，
    // 真实原因会被吞掉。这里把它透传到 error.message，让各处的 notify(error.message)
    // 能直接显示后端给出的中文原因（如「未配置API Key」）。
    const data = error.response?.data as { detail?: unknown } | undefined;
    if (data?.detail) {
      const detail = Array.isArray(data.detail)
        ? data.detail.map((item: any) => item?.msg ?? String(item)).join('；')
        : String(data.detail);
      error.message = detail;
    }
    return Promise.reject(error);
  },
);

export const get = async <T = unknown>(
  url: string,
  params?: Record<string, any>,
  config?: AxiosRequestConfig,
): Promise<T> => {
  const response = await request.get<T>(url, { params, ...config });
  return response.data;
};

export const post = async <T = unknown>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> => {
  const response = await request.post<T>(url, data, config);
  return response.data;
};

export const put = async <T = unknown>(
  url: string,
  data?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> => {
  const response = await request.put<T>(url, data, config);
  return response.data;
};

export const del = async <T = unknown>(
  url: string,
  config?: AxiosRequestConfig,
): Promise<T> => {
  const response = await request.delete<T>(url, config);
  return response.data;
};

export default request;
