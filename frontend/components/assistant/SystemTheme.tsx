"use client";

import { useEffect } from "react";

/**
 * On its own, the module follows the system's light or dark setting by putting the `dark`
 * class on the page. Inside Flow, Flow sets the class and this is not used.
 */
export function SystemTheme() {
  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => document.documentElement.classList.toggle("dark", query.matches);
    apply();
    query.addEventListener("change", apply);
    return () => query.removeEventListener("change", apply);
  }, []);
  return null;
}
