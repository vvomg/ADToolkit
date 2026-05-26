/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Plus Jakarta Sans", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
      colors: {
        base:     "#24273a",
        mantle:   "#1e2030",
        crust:    "#181926",
        surface0: "#363a4f",
        surface1: "#494d64",
        surface2: "#5b6078",
        overlay0: "#6e738d",
        overlay1: "#8087a2",
        subtext:  "#a5adcb",
        text:     "#cad3f5",
        blue:     "#8aadf4",
        teal:     "#8bd5ca",
        green:    "#a6da95",
        yellow:   "#eed49f",
        red:      "#ed8796",
        mauve:    "#c6a0f6",
        peach:    "#f5a97f",
      },
      borderRadius: {
        lg: "0.5rem",
        md: "calc(0.5rem - 2px)",
        sm: "calc(0.5rem - 4px)",
      },
    },
  },
  plugins: [],
}
