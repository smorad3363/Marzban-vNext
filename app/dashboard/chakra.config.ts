import { extendTheme } from "@chakra-ui/react";
export const theme = extendTheme({
  config: {
    initialColorMode: "system",
    useSystemColorMode: true,
  },
  shadows: {
    outline: "0 0 0 3px rgba(37, 99, 235, 0.26)",
    panel: "0 1px 2px rgba(15, 23, 42, 0.2), 0 14px 34px rgba(15, 23, 42, 0.16)",
    elevated: "0 24px 58px rgba(2, 6, 23, 0.38)",
  },
  fonts: {
    heading: `Fira Sans, Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif`,
    body: `Fira Sans, Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif`,
    mono: `Fira Code, IBM Plex Mono, Consolas, monospace`,
  },
  colors: {
    "light-border": "#d7deea",
    surface: {
      light: "#ffffff",
      dark: "#111827",
    },
    primary: {
      50: "#eff6ff",
      100: "#dbeafe",
      200: "#bfdbfe",
      300: "#93c5fd",
      400: "#60a5fa",
      500: "#3b82f6",
      600: "#2563eb",
      700: "#1d4ed8",
      800: "#1e40af",
      900: "#1e3a8a",
    },
    gold: {
      50: "#fff9e8",
      100: "#f8e9b6",
      200: "#ecd483",
      300: "#ddbd55",
      400: "#caa53d",
      500: "#ad8529",
      600: "#896720",
      700: "#684e1c",
      800: "#493817",
      900: "#2d2413",
    },
    gray: {
      750: "#172235",
    },
  },
  styles: {
    global: {
      body: {
        bg: "#f4f7fb",
        color: "gray.900",
        lineHeight: "1.5",
        _dark: {
          bg: "#0b1020",
          color: "gray.100",
        },
      },
      'html[lang^="fa"]': {
        "--chakra-fonts-heading": `Vazirmatn, Fira Sans, sans-serif`,
        "--chakra-fonts-body": `Vazirmatn, Fira Sans, sans-serif`,
      },
      "::selection": {
        bg: "primary.200",
        color: "gray.900",
      },
    },
  },
  components: {
    Button: {
      baseStyle: {
        borderRadius: "8px",
        fontWeight: "650",
        transitionProperty: "background-color, border-color, color",
        transitionDuration: "120ms",
      },
      sizes: {
        md: { h: "42px", px: 4 },
      },
    },
    Card: {
      baseStyle: {
        container: {
          bg: "#ffffff",
          color: "gray.900",
          borderColor: "#d7deea",
          _dark: { bg: "#111827", color: "gray.100", borderColor: "#273449" },
        },
      },
    },
    Modal: {
      baseStyle: {
        dialog: {
          bg: "#ffffff",
          color: "gray.900",
          borderWidth: "1px",
          borderColor: "#d7deea",
          _dark: { bg: "#111827", color: "gray.100", borderColor: "#273449" },
        },
        header: { borderColor: "#d7deea", _dark: { borderColor: "#273449" } },
        footer: { borderColor: "#d7deea", _dark: { borderColor: "#273449" } },
      },
    },
    Alert: {
      baseStyle: {
        container: {
          borderRadius: "8px",
          fontSize: "sm",
        },
      },
    },
    Select: {
      baseStyle: {
        field: {
          bg: "#ffffff",
          color: "gray.900",
          borderColor: "#b8c4d6",
          borderRadius: "6px",
          _dark: {
            bg: "#111827",
            color: "gray.100",
            borderColor: "#3b4b65",
            borderRadius: "6px",
          },
        },
      },
      sizes: {
        sm: { field: { minH: "44px", fontSize: "sm", px: 3, paddingInlineEnd: 8 } },
        md: { field: { minH: "44px", fontSize: "sm", px: 3, paddingInlineEnd: 8 } },
      },
    },
    FormHelperText: {
      baseStyle: {
        fontSize: "xs",
        color: "gray.600",
        _dark: { color: "gray.400" },
      },
    },
    FormLabel: {
      baseStyle: {
        fontSize: "sm",
        fontWeight: "medium",
        mb: "1",
        lineHeight: "1.7",
        whiteSpace: "normal",
        overflowWrap: "anywhere",
        color: "gray.700",
        _dark: { color: "gray.300" },
      },
    },
    Input: {
      baseStyle: {
        addon: {
          _dark: {
            borderColor: "gray.600",
            _placeholder: {
              color: "gray.500",
            },
          },
        },
        field: {
          borderRadius: "10px",
          bg: "white",
          color: "gray.900",
          borderColor: "#b8c4d6",
          _focusVisible: {
            boxShadow: "none",
            borderColor: "primary.200",
            outlineColor: "primary.200",
          },
          _dark: {
            bg: "whiteAlpha.50",
            color: "gray.100",
            borderColor: "gray.600",
            _disabled: {
              color: "gray.400",
              borderColor: "gray.500",
            },
            _placeholder: {
              color: "gray.500",
            },
          },
        },
      },
      sizes: {
        sm: {
          field: { minH: "44px", fontSize: "sm", px: 3 },
          addon: { minH: "44px", fontSize: "sm", px: 3 },
        },
        md: {
          field: { minH: "44px", fontSize: "sm", px: 3 },
          addon: { minH: "44px", fontSize: "sm", px: 3 },
        },
      },
    },
    Textarea: {
      baseStyle: {
        bg: "white",
        color: "gray.900",
        borderColor: "#b8c4d6",
        lineHeight: "1.8",
        _placeholder: { color: "gray.500" },
        _focusVisible: { borderColor: "primary.300", boxShadow: "outline" },
        _dark: { bg: "whiteAlpha.50", color: "gray.100", borderColor: "gray.600" },
      },
    },
    Table: {
      baseStyle: {
        table: {
          borderCollapse: "separate",
          borderSpacing: 0,
        },
        thead: {
          borderBottomColor: "#33483b",
        },
        th: {
          background: "#edf2f8",
          color: "gray.600",
          fontSize: "xs",
          letterSpacing: "0.04em",
          borderColor: "#d7deea !important",
          borderBottomColor: "#d7deea !important",
          borderTop: "1px solid ",
          borderTopColor: "#d7deea !important",
          _first: {
            borderInlineStart: "1px solid",
            borderColor: "#d7deea !important",
          },
          _last: {
            borderInlineEnd: "1px solid",
            borderColor: "#d7deea !important",
          },
          _dark: {
            borderColor: "gray.600 !important",
            background: "#172033",
          },
        },
        td: {
          transition: "background-color .12s ease-out",
          py: 4,
          color: "gray.800",
          borderColor: "#d7deea",
          borderBottomColor: "#d7deea !important",
          _first: {
            borderInlineStart: "1px solid",
            borderColor: "#d7deea",
            _dark: {
              borderColor: "gray.600",
            },
          },
          _last: {
            borderInlineEnd: "1px solid",
            borderColor: "#d7deea",
            _dark: {
              borderColor: "gray.600",
            },
          },
          _dark: {
            color: "gray.100",
            borderColor: "gray.600",
            borderBottomColor: "gray.600 !important",
          },
        },
        tr: {
          "&.interactive": {
            cursor: "pointer",
            _hover: {
              "& > td": {
                  bg: "#16251c",
              },
              _dark: {
                "& > td": {
                  bg: "#16251c",
                },
              },
            },
          },
          _last: {
            "& > td": {
              _first: {
                borderEndStartRadius: "8px",
              },
              _last: {
                borderEndEndRadius: "8px",
              },
            },
          },
        },
      },
    },
  },
});
