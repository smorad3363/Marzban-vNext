import { Box, Flex } from "@chakra-ui/react";
import { FC, PropsWithChildren } from "react";
import { Footer } from "./Footer";
import { Header } from "./Header";

export const AppShell: FC<PropsWithChildren> = ({ children }) => (
  <Flex minH="100vh" align="stretch" direction={{ base: "column", lg: "row" }} className="operations-shell">
    <Header />
    <Flex
      as="main"
      minW={0}
      flex="1"
      direction="column"
      id="main-content"
      px={{ base: 4, md: 7, xl: 9 }}
      py={{ base: 5, md: 7 }}
    >
      <Box w="full" maxW="none" minW={0} flex="1">
        {children}
      </Box>
      <Footer mt={8} />
    </Flex>
  </Flex>
);
