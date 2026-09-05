import { useEffect } from "react";
import { useQuery } from "react-query";
import { fetch } from "service/http";
import { SystemBranding } from "types/Admin";

const fallback: SystemBranding = {
  panel_name: "Operations Console",
  login_title: "Secure operator access",
  description: "Clear signals. Controlled access.",
  logo_url: null,
  favicon_url: null,
};

export const useBranding = () => {
  const query = useQuery<SystemBranding, Error>("system-branding", () => fetch("/branding/public"), {
    staleTime: 300_000,
    retry: 1,
  });
  const branding = query.data && typeof query.data.panel_name === "string" && typeof query.data.login_title === "string"
    ? query.data
    : fallback;
  useEffect(() => {
    document.title = branding.panel_name;
    const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (icon) icon.href = branding.favicon_url || "/statics/favicon/operations-mark.svg";
  }, [branding.panel_name, branding.favicon_url]);
  return { ...query, branding };
};
