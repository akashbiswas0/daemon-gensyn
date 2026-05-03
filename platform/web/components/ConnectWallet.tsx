"use client";

import { useEffect, useState } from "react";
import { clearAuthSession, writeAuthSession } from "../lib/auth";
import { ZEROG_GALILEO } from "../lib/zerog-galileo";

import { clientApiBase } from "../lib/clientApiBase";

const API_BASE = clientApiBase();

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
    };
  }
}

export function ConnectWallet() {
  const [status, setStatus] = useState("Wallet not connected. 0G Galileo required for NodeHub.");
  const [walletAddress, setWalletAddress] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const token = localStorage.getItem("nodehub_token");
    const wallet = localStorage.getItem("nodehub_wallet");
    if (token && wallet) {
      setWalletAddress(wallet);
      setStatus(`Connected on 0G Galileo: ${wallet.slice(0, 6)}...${wallet.slice(-4)}`);
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

  async function ensureZeroGGalileo() {
    if (!window.ethereum) {
      throw new Error("No injected wallet found");
    }
    const currentChainId = (await window.ethereum.request({ method: "eth_chainId" })) as string;
    if (currentChainId?.toLowerCase() === ZEROG_GALILEO.chainId) {
      return;
    }
    try {
      setStatus("Switching to 0G Galileo...");
      await window.ethereum.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: ZEROG_GALILEO.chainId }]
      });
    } catch (error) {
      const switchError = error as { code?: number };
      if (switchError.code !== 4902) {
        throw new Error("Please switch your wallet to 0G Galileo");
      }
      setStatus("Adding 0G Galileo to wallet...");
      await window.ethereum.request({
        method: "wallet_addEthereumChain",
        params: [ZEROG_GALILEO]
      });
      await window.ethereum.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: ZEROG_GALILEO.chainId }]
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
      await ensureZeroGGalileo();
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
      setStatus(`Connected on 0G Galileo: ${wallet.slice(0, 6)}...${wallet.slice(-4)}`);
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
