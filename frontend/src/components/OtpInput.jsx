// 6-cell one-time-code input with auto-advancing focus. Controlled component:
// the parent owns the string `value` (0–6 chars); `onChange` receives the new
// string. Supports paste of a full code, Backspace to step back, and arrow keys.
import { useRef } from "react";

const LEN = 6;

export default function OtpInput({ value = "", onChange, disabled = false }) {
  const refs = useRef([]);
  const chars = value.split("").slice(0, LEN);

  const setCharAt = (i, ch) => {
    const arr = value.split("");
    while (arr.length < LEN) arr.push("");
    arr[i] = ch;
    onChange(arr.join("").slice(0, LEN));
  };

  const handleChange = (i) => (e) => {
    const digit = e.target.value.replace(/\D/g, "").slice(-1); // keep last typed digit
    if (!digit) { setCharAt(i, ""); return; }
    setCharAt(i, digit);
    if (i < LEN - 1) refs.current[i + 1]?.focus();
  };

  const handleKeyDown = (i) => (e) => {
    if (e.key === "Backspace") {
      if (chars[i]) { setCharAt(i, ""); return; }
      if (i > 0) { refs.current[i - 1]?.focus(); setCharAt(i - 1, ""); }
    } else if (e.key === "ArrowLeft" && i > 0) {
      refs.current[i - 1]?.focus();
    } else if (e.key === "ArrowRight" && i < LEN - 1) {
      refs.current[i + 1]?.focus();
    }
  };

  const handlePaste = (e) => {
    const digits = (e.clipboardData.getData("text") || "").replace(/\D/g, "").slice(0, LEN);
    if (!digits) return;
    e.preventDefault();
    onChange(digits);
    refs.current[Math.min(digits.length, LEN - 1)]?.focus();
  };

  return (
    <div className="flex gap-2" onPaste={handlePaste}>
      {Array.from({ length: LEN }).map((_, i) => (
        <input
          key={i}
          ref={(el) => (refs.current[i] = el)}
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={1}
          disabled={disabled}
          value={chars[i] || ""}
          onChange={handleChange(i)}
          onKeyDown={handleKeyDown(i)}
          className="w-11 h-12 text-center text-lg font-bold rounded-lg border border-border bg-white focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/30 disabled:bg-gray-50 disabled:text-gray-400"
        />
      ))}
    </div>
  );
}
