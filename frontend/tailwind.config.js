export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--border))",
        ring: "hsl(var(--primary))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: { DEFAULT: "hsl(var(--primary))", foreground: "#fff" },
        secondary: { DEFAULT: "#f1f5f9", foreground: "#334155" },
        muted: { DEFAULT: "#f5f7fa", foreground: "#697586" },
        accent: { DEFAULT: "#eef5ff", foreground: "#2563eb" },
        destructive: { DEFAULT: "#dc2626", foreground: "#fff" },
        card: { DEFAULT: "#fff", foreground: "#172b4d" },
        popover: { DEFAULT: "#fff", foreground: "#172b4d" },
      },
      borderRadius: { lg: "12px", md: "8px", sm: "6px" },
    },
  },
  plugins: [],
};
