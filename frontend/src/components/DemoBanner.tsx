import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchConfig } from "../lib/api";

const ORIGINAL_TITLE = document.title;

export default function DemoBanner() {
  const config = useQuery({ queryKey: ["config"], queryFn: fetchConfig });
  const isDemo = config.data?.app_mode === "demo";

  useEffect(() => {
    document.title = isDemo ? `[DEMO] ${ORIGINAL_TITLE}` : ORIGINAL_TITLE;
  }, [isDemo]);

  if (!isDemo) return null;

  return (
    <div
      style={{
        background: "linear-gradient(135deg, #e8485f, #c23652)",
        color: "white",
        padding: "8px 16px",
        textAlign: "center",
        fontWeight: 700,
        fontSize: 13,
      }}
    >
      DEMO ENVIRONMENT: compliance statuses are test fixtures, not halal rulings.
    </div>
  );
}
