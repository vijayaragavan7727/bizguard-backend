// ============================================================
// BizGuard — API Service
// FILE: src/api/index.ts
// ============================================================

const BASE_URL = 'http://10.67.204.124:8000';

let authToken: string | null = null;
let currentUserId: string | null = null;

export const setAuthToken = (token: string) => { authToken = token; };
export const setUserId = (id: string) => { currentUserId = id; };
export const getAuthToken = () => authToken;
export const getUserId = () => currentUserId;

const apiCall = async (endpoint: string, options: RequestInit = {}) => {
    const headers: any = {
        'Content-Type': 'application/json',
        ...(authToken && { Authorization: `Bearer ${authToken}` }),
    };
    const response = await fetch(`${BASE_URL}${endpoint}`, { ...options, headers });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'API error');
    return data;
};

// ── Auth ──────────────────────────────────────────────────────
export const sendOTP = (phone: string, shopName?: string, ownerName?: string, location?: string) =>
    apiCall('/api/v1/auth/send-otp', {
        method: 'POST',
        body: JSON.stringify({
            phone_number: phone,
            shop_name: shopName,
            owner_name: ownerName,
            location: location,
        }),
    });

export const verifyOTP = (phone: string, otp: string) =>
    apiCall('/api/v1/auth/verify-otp', {
        method: 'POST',
        body: JSON.stringify({ phone_number: phone, otp: otp }),
    });

// ── Dashboard ─────────────────────────────────────────────────
export const getDashboardSummary = (userId: string) =>
    apiCall(`/api/v1/dashboard/summary?user_id=${userId}`);

// ── Transactions ──────────────────────────────────────────────
export const getTransactions = (userId: string, category?: string) =>
    apiCall(`/api/v1/transactions?user_id=${userId}${category ? `&category=${category}` : ''}`);

// ── Bulk Sync ─────────────────────────────────────────────────
export const bulkSyncTransactions = (userId: string, transactions: any[]) =>
    apiCall('/api/v1/sync/bulk-transactions', {
        method: 'POST',
        body: JSON.stringify({ user_id: userId, transactions }),
    });

// ── AI Advisor ────────────────────────────────────────────────
export const askAdvisor = (userId: string, query: string) =>
    apiCall(`/api/v1/advisor/chat?user_id=${userId}`, {
        method: 'POST',
        body: JSON.stringify({ query, include_context: true }),
    });

// ── Anomalies ─────────────────────────────────────────────────
export const getAnomalies = (userId: string) =>
    apiCall(`/api/v1/anomalies?user_id=${userId}`);