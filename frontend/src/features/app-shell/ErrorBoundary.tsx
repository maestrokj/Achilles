import { Component, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { SystemScreen } from "@/components/SystemScreen";

/** 500 stub of system-screens.html — a full-collapse fallback for an uncaught
 * render error, so a crash shows an honest screen instead of a blank page.
 * i18n works here without a provider: the instance is initialized on import. */
function ErrorFallback() {
  const { t } = useTranslation();
  return (
    <SystemScreen
      code="500"
      title={t("app.errorBoundary.title")}
      body={t("app.errorBoundary.body")}
    >
      <Button
        variant="outline"
        onClick={() => {
          window.location.reload();
        }}
      >
        {t("app.errorBoundary.retry")}
      </Button>
    </SystemScreen>
  );
}

/** Root boundary wrapping the whole provider tree — catches render errors from
 * providers and the router alike and swaps in the 500 fallback. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }

  componentDidCatch(error: unknown): void {
    console.error("Unhandled render error:", error);
  }

  render(): ReactNode {
    if (this.state.hasError) return <ErrorFallback />;
    return this.props.children;
  }
}
