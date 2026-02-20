"use client";

import React, { useState, useEffect } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import "@copilotkit/react-ui/styles.css";

// Error boundary catches CopilotKit render errors (e.g. "Agent 'default' not found")
// and falls back to rendering children without CopilotKit.
class CopilotErrorBoundary extends React.Component<
  { children: React.ReactNode; fallback: React.ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: React.ReactNode; fallback: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    console.warn("[DealGraph] CopilotKit unavailable:", error.message);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}

export default function Providers({ children }: { children: React.ReactNode }) {
  const [copilotReady, setCopilotReady] = useState(false);

  const runtimeUrl = process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL}/copilotkit`
    : "http://localhost:8000/copilotkit";

  // Only attempt CopilotKit if the backend endpoint responds.
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);

    fetch(runtimeUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal: controller.signal,
    })
      .then(() => setCopilotReady(true))
      .catch(() => {
        // Backend not reachable - run without CopilotKit
      })
      .finally(() => clearTimeout(timeout));
  }, [runtimeUrl]);

  if (copilotReady) {
    return (
      <CopilotErrorBoundary fallback={<>{children}</>}>
        <CopilotKit runtimeUrl={runtimeUrl}>
          {children}
        </CopilotKit>
      </CopilotErrorBoundary>
    );
  }

  return <>{children}</>;
}
