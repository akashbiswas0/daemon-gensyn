"use client";

import { useEffect, useState } from "react";
import { clearAuthSession, writeAuthSession } from "../lib/auth";
import { BASE_SEPOLIA } from "../lib/base-sepolia";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
    };
  }
}

export function ConnectWallet() {
  const [status, setStatus] = useState("Wallet not connected. Base Sepolia required for NodeHub.");
  const [walletAddress, setWalletAddress] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const token = localStorage.getItem("nodehub_token");
    const wallet = localStorage.getItem("nodehub_wallet");
    if (token && wallet) {
      setWalletAddress(wallet);
      setStatus(`Connected on Base Sepolia: ${wallet.slice(0, 6)}...${wallet.slice(-4)}`);
    }
  }, []);

  function getWalletErrorMessage(error: unknown) {
    if (error instanceof Error && error.message) {
      return error.message;
    }
    if (typeof error === "object" && error !== null) {
      const providerError = error as {
        code?: number;
        message?: string;
        data?: { originalError?: { message?: string } };
      };
      if (providerError.code === 4001) {
        return "Wallet request was rejected.";
      }
      if (providerError.message) {
        return providerError.message;
      }
      if (providerError.data?.originalError?.message) {
        return providerError.data.originalError.message;
      }
    }
    return "Wallet connection failed";
  }

  async function ensureBaseSepolia() {
    if (!window.ethereum) {
      throw new Error("No injected wallet found");
    }
    const currentChainId = (await window.ethereum.request({ method: "eth_chainId" })) as string;
    if (currentChainId?.toLowerCase() === BASE_SEPOLIA.chainId) {
      return;
    }
    try {
      setStatus("Switching to Base Sepolia...");
      await window.ethereum.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: BASE_SEPOLIA.chainId }]
      });
    } catch (error) {
      const switchError = error as { code?: number };
      if (switchError.code !== 4902) {
        throw new Error("Please switch your wallet to Base Sepolia");
      }
      setStatus("Adding Base Sepolia to wallet...");
      await window.ethereum.request({
        method: "wallet_addEthereumChain",
        params: [BASE_SEPOLIA]
      });
      await window.ethereum.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: BASE_SEPOLIA.chainId }]
      });
    }
  }

  async function connect() {
    try {
      setStatus("Connecting...");
      if (!window.ethereum) {
        setStatus("No injected wallet found");
        return;
      }
      await ensureBaseSepolia();
      const accounts = (await window.ethereum.request({ method: "eth_requestAccounts" })) as string[];
      const wallet = accounts[0];
      const challengeRes = await fetch(`${API_BASE}/auth/challenge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wallet_address: wallet })
      });
      if (!challengeRes.ok) {
        throw new Error(`Challenge request failed (${challengeRes.status})`);
      }
      const challenge = await challengeRes.json();
      const signature = (await window.ethereum.request({
        method: "personal_sign",
        params: [challenge.challenge, wallet]
      })) as string;
      const verifyRes = await fetch(`${API_BASE}/auth/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ wallet_address: wallet, signature })
      });
      if (!verifyRes.ok) {
        throw new Error(`Signature verification failed (${verifyRes.status})`);
      }
      const token = await verifyRes.json();
      writeAuthSession(token.access_token, wallet);
      setWalletAddress(wallet);
      setStatus(`Connected on Base Sepolia: ${wallet.slice(0, 6)}...${wallet.slice(-4)}`);
    } catch (error) {
      const message = getWalletErrorMessage(error);
      setStatus(message);
      console.error("Wallet connection error", {
        message,
        error,
      });
    }
  }

  function disconnect() {
    clearAuthSession();
    setWalletAddress(null);
    setStatus("Wallet disconnected.");
  }

  return (
    <div className="row">
      <button className="button" onClick={connect}>
        {walletAddress ? "Reconnect Wallet" : "Connect Wallet"}
      </button>
      {walletAddress ? (
        <button className="button secondary" onClick={disconnect}>Disconnect</button>
      ) : null}
      <span className="muted">{status}</span>
    </div>
  );
}
