import { createTheme } from "@mantine/core";

const interfaceFont =
  '"Geist", "Segoe UI Variable", "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif';

export const parseriumTheme = createTheme({
  primaryColor: "parseriumEmber",
  primaryShade: { light: 7, dark: 7 },
  defaultRadius: "xs",
  colors: {
    parseriumEmber: [
      "#FFF0E9",
      "#FFE0D1",
      "#FFC0A3",
      "#FF976E",
      "#F97343",
      "#ED5A27",
      "#E44A12",
      "#BF380A",
      "#982B09",
      "#762208",
    ],
    parseriumGreen: [
      "#EDF8EF",
      "#D8EEDF",
      "#B1DDBD",
      "#82C895",
      "#55AD70",
      "#378F54",
      "#26783C",
      "#1E6031",
      "#184C28",
      "#123A1F",
    ],
    parseriumRed: [
      "#FCEDEC",
      "#F7D8D5",
      "#EEB0AA",
      "#E18379",
      "#CF5B4E",
      "#BF4034",
      "#B7352A",
      "#922B23",
      "#74231D",
      "#591B17",
    ],
  },
  fontFamily: interfaceFont,
  fontFamilyMonospace:
    'ui-monospace, "Cascadia Code", "SFMono-Regular", Consolas, monospace',
  headings: {
    fontFamily: interfaceFont,
    fontWeight: "600",
  },
});
