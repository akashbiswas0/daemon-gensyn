"use client";

import { useEffect, useState } from "react";
import { AUTH_EVENT, readAuthSession, type AuthSession } from "../lib/auth";

export function useAuthSession() {
  const [session, setSession] = useState<AuthSession>({ token: null, wallet: null });

  useEffect(() => {
    const sync = () => setSession(readAuthSession());
    sync();
    window.addEventListener(AUTH_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(AUTH_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  return session;
}
