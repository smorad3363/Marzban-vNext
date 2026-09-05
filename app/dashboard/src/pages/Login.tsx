import {
  Alert,
  AlertDescription,
  AlertIcon,
  Badge,
  Box,
  Button,
  Flex,
  FormControl,
  Grid,
  HStack,
  SimpleGrid,
  Text,
  VStack,
} from "@chakra-ui/react";
import { ArrowRightOnRectangleIcon, ServerStackIcon, ShieldCheckIcon } from "@heroicons/react/24/outline";
import { zodResolver } from "@hookform/resolvers/zod";
import { BrandMark } from "components/BrandMark";
import { resetDashboardState } from "contexts/DashboardContext";
import { Footer } from "components/Footer";
import { Input } from "components/Input";
import { FC, useEffect, useState } from "react";
import { FieldValues, useForm } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router-dom";
import { fetch } from "service/http";
import { removeAuthToken, setAuthToken } from "utils/authStorage";
import { localizedApiError } from "utils/apiError";
import { useBranding } from "hooks/useBranding";
import { z } from "zod";

const schema = z.object({
  username: z.string().min(1, "login.fieldRequired"),
  password: z.string().min(1, "login.fieldRequired"),
});

const SignalTile: FC<{ number: string; symbol: string; label: string }> = ({ number, symbol, label }) => (
  <Box border="1px solid" borderColor="rgba(96, 165, 250, 0.3)" bg="rgba(11, 16, 32, 0.78)" p={4} minH="116px">
    <Text fontFamily="mono" fontSize="xs" color="primary.300">{number}</Text>
    <Text fontFamily="mono" fontSize="4xl" fontWeight="700" lineHeight="1" mt={2}>{symbol}</Text>
    <Text fontSize="xs" color="gray.400" mt={3} textTransform="uppercase" letterSpacing="0.12em">{label}</Text>
  </Box>
);

export const Login: FC = () => {
  const { branding } = useBranding();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { t } = useTranslation();
  const location = useLocation();
  const { register, formState: { errors }, handleSubmit } = useForm({ resolver: zodResolver(schema) });

  useEffect(() => {
    removeAuthToken();
    resetDashboardState();
    if (location.pathname !== "/login") navigate("/login", { replace: true });
  }, []);

  const login = (values: FieldValues) => {
    setError("");
    const formData = new FormData();
    formData.append("username", values.username);
    formData.append("password", values.password);
    formData.append("grant_type", "password");
    setLoading(true);
    fetch("/admin/token", { method: "post", body: formData })
      .then(({ access_token: token }) => {
        if (typeof token !== "string" || !token) {
          throw new Error("Login response did not contain an access token");
        }
        setAuthToken(token);
        resetDashboardState();
        navigate("/");
      })
      .catch((err) => setError(localizedApiError(err)))
      .finally(setLoading.bind(null, false));
  };

  return (
    <Grid minH="100vh" templateColumns={{ base: "1fr", lg: "minmax(420px, .82fr) 1.18fr" }} bg="#f3f6f2" _dark={{ bg: "#09130e" }}>
      <Flex direction="column" minH="100vh" minW={0} px={{ base: 6, md: 12 }} py={{ base: 6, md: 8 }} borderEnd={{ lg: "1px solid" }} borderColor={{ lg: "gray.200" }} _dark={{ borderColor: "rgba(91,132,108,.32)" }}>
        <HStack justifyContent="space-between" w="full">
          <HStack spacing={3}>
            {branding.logo_url ? <Box as="img" src={branding.logo_url} alt={`${branding.panel_name} logo`} boxSize="44px" objectFit="contain" /> : <BrandMark aria-hidden="true" boxSize="44px" filter="drop-shadow(0 9px 22px rgba(37,99,235,.24))" />}
            <Box>
              <Text fontSize="sm" fontWeight="800" color="primary.600" _dark={{ color: "primary.300" }}>{branding.panel_name}</Text>
              <Text fontSize="xs" color="gray.500">Secure operations workspace</Text>
            </Box>
          </HStack>
        </HStack>

        <Flex flex="1" align="center" justify="center" py={12}>
          <Box w="full" maxW="390px">
            <Badge colorScheme="green" variant="subtle" px={2.5} py={1} borderRadius="4px" fontFamily="mono" letterSpacing=".08em">AUTHORIZED PERSONNEL</Badge>
            <VStack alignItems="flex-start" w="full" spacing={2} mt={5}>
              <Text as="h1" fontSize={{ base: "3xl", md: "4xl" }} lineHeight="1.08" letterSpacing="-0.035em" fontWeight="700">{branding.login_title}</Text>
              <Text color="gray.600" _dark={{ color: "gray.400" }} maxW="42ch">{branding.description || t("login.welcomeBack")}</Text>
            </VStack>

            <Box w="full" pt="7">
              <form onSubmit={handleSubmit(login)}>
                <VStack rowGap={3}>
                  <FormControl><Input w="full" label={t("username")} placeholder={t("username")} autoComplete="username" {...register("username")} error={t(errors?.username?.message as string)} /></FormControl>
                  <FormControl><Input w="full" label={t("password")} type="password" placeholder={t("password")} autoComplete="current-password" {...register("password")} error={t(errors?.password?.message as string)} /></FormControl>
                  {error && <Alert status="error" rounded="md"><AlertIcon /><AlertDescription>{error}</AlertDescription></Alert>}
                  <Button isLoading={loading} type="submit" w="full" colorScheme="primary" mt={2} h="44px" rightIcon={<ArrowRightOnRectangleIcon width="18px" aria-hidden="true" />}>{t("login")}</Button>
                </VStack>
              </form>
            </Box>
          </Box>
        </Flex>
        <Footer />
      </Flex>

      <Box display={{ base: "none", lg: "block" }} position="relative" overflow="hidden" bg="#0b1020" color="white">
        <Box
          aria-hidden="true"
          position="absolute"
          inset="0"
          bgImage="url('/statics/images/rv-operations-login-blurred.png')"
          bgSize="cover"
          bgPosition="72% center"
          bgRepeat="no-repeat"
          filter="saturate(.8) contrast(1.06)"
          transform="scale(1.01)"
        />
        <Box
          aria-hidden="true"
          position="absolute"
          inset={0}
          bgGradient="linear(to-r, rgba(11,16,32,.96) 0%, rgba(11,16,32,.84) 38%, rgba(11,16,32,.4) 72%, rgba(11,16,32,.2) 100%), linear(to-b, rgba(11,16,32,.14), rgba(11,16,32,.78))"
        />
        <Box aria-hidden="true" position="absolute" inset={0} className="operations-grid" opacity=".2" />
        <Flex position="relative" minH="100vh" direction="column" justify="space-between" p={{ lg: 10, xl: 14 }}>
          <HStack justify="space-between" fontFamily="mono" fontSize="xs" color="primary.200" letterSpacing=".12em">
            <HStack><Box boxSize="7px" borderRadius="full" bg="primary.300" boxShadow="0 0 14px #60a5fa" /><Text>SYSTEM STABLE</Text></HStack>
            <Text>OPS // 01</Text>
          </HStack>

          <Box maxW="680px">
            <SimpleGrid columns={3} gap={3} maxW="500px" mb={10}>
              <SignalTile number="01" symbol="N" label="Network" />
              <SignalTile number="02" symbol="O" label="Observe" />
              <SignalTile number="03" symbol="C" label="Control" />
            </SimpleGrid>
            <Text as="h2" fontFamily="mono" fontSize={{ lg: "4xl", xl: "5xl" }} fontWeight="700" lineHeight="1.08" letterSpacing="-.045em">Signals stay clear.<br /><Text as="span" color="primary.300">Control stays close.</Text></Text>
            <Text color="gray.400" mt={5} maxW="48ch">A private operations surface built for exact decisions, clear signals and controlled access.</Text>
          </Box>

          <HStack spacing={6} color="gray.400" fontSize="xs">
            <HStack><ShieldCheckIcon width="17px" aria-hidden="true" /><Text>Protected access</Text></HStack>
            <HStack><ServerStackIcon width="17px" aria-hidden="true" /><Text>Controlled environment</Text></HStack>
          </HStack>
        </Flex>
      </Box>
    </Grid>
  );
};

export default Login;
