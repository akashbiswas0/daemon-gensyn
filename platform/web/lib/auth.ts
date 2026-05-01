export const TOKEN_KEY = "nodehub_token";
export const WALLET_KEY = "nodehub_wallet";
export const AUTH_EVENT = "nodehub-auth-updated";

export type AuthSession = {
  token: string | null;
  wallet: string | null;
};

export function readAuthSession(): AuthSession {
  if (typeof window === "undefined") {
    return { token: null, wallet: null };
  }
  return {
    token: localStorage.getItem(TOKEN_KEY),
    wallet: localStorage.getItem(WALLET_KEY),
  };
}

export function writeAuthSession(token: string, wallet: string) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(WALLET_KEY, wallet);
  window.dispatchEvent(new Event(AUTH_EVENT));
}

export function clearAuthSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(WALLET_KEY);
  window.dispatchEvent(new Event(AUTH_EVENT));
}
