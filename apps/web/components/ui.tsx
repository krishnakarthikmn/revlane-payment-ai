import type { ReactNode } from "react";

import type { Tone } from "@/lib/console/view";

export function Pill({
  tone,
  className = "",
  children,
}: {
  tone: Tone;
  className?: string;
  children: ReactNode;
}) {
  return <span className={`pill pill-${tone} ${className}`}>{children}</span>;
}

/** Renders sample copy that marks emphasis as **bold**. */
export function Emphasis({ text }: { text: string }) {
  return (
    <>
      {text.split("**").map((part, i) =>
        i % 2 === 1 ? <b key={i}>{part}</b> : <span key={i}>{part}</span>,
      )}
    </>
  );
}

const STAR = "M12 2 L13.6 10.4 L22 12 L13.6 13.6 L12 22 L10.4 13.6 L2 12 L10.4 10.4 Z";

export function Star({
  size,
  spin = false,
  className = "",
}: {
  size: number;
  spin?: boolean;
  className?: string;
}) {
  return (
    <svg
      className={`${spin ? "spin" : ""} ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d={STAR} fill="#D97757" />
    </svg>
  );
}

export function ClockIcon({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="8.5" stroke="#3D3C38" strokeWidth="1.6" fill="none" />
      <path
        d="M12 7.5 V12 L15 14"
        stroke="#3D3C38"
        strokeWidth="1.6"
        fill="none"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function StrokeIcon({
  d,
  size = 16,
  color = "#3D3C38",
  width = 1.6,
}: {
  d: string;
  size?: number;
  color?: string;
  width?: number;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      <path
        d={d}
        stroke={color}
        strokeWidth={width}
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
