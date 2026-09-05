import { Box, BoxProps } from "@chakra-ui/react";
import { FC } from "react";

export const BrandMark: FC<BoxProps> = (props) => (
  <Box
    as="svg"
    viewBox="0 0 56 56"
    role="img"
    aria-label="Operations Console"
    flexShrink={0}
    {...props}
  >
    <defs>
      <linearGradient id="operations-mark" x1="7" y1="4" x2="49" y2="52">
        <stop stopColor="#2563EB" />
        <stop offset="1" stopColor="#0891B2" />
      </linearGradient>
    </defs>
    <rect x="2" y="2" width="52" height="52" rx="13" fill="#0B1020" />
    <circle cx="28" cy="28" r="18" fill="none" stroke="url(#operations-mark)" strokeWidth="3" />
    <path d="M13 31c9-10 22-13 31-5M17 41c6-12 15-19 27-21" fill="none" stroke="#67E8F9" strokeWidth="2.5" strokeLinecap="round" />
    <circle cx="18" cy="20" r="3.5" fill="#F8FAFC" /><circle cx="40" cy="35" r="3.5" fill="#F8FAFC" />
  </Box>
);
