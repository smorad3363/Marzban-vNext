import {
  Box,
  Button,
  chakra,
  Collapse,
  Flex,
  HStack,
  IconButton,
  Image,
  SimpleGrid,
  Spacer,
  Stack,
  Text,
  useColorMode,
} from "@chakra-ui/react";
import {
  ArrowLeftOnRectangleIcon,
  ChartPieIcon,
  ClipboardDocumentListIcon,
  Cog6ToothIcon,
  DocumentMinusIcon,
  LinkIcon,
  SquaresPlusIcon,
  UserGroupIcon,
  UsersIcon,
  ShieldCheckIcon,
  RectangleStackIcon,
  Bars3Icon,
  XMarkIcon,
  MoonIcon,
  SunIcon,
} from "@heroicons/react/24/outline";
import { resetDashboardState, useDashboard } from "contexts/DashboardContext";
import useGetUser from "hooks/useGetUser";
import { useBranding } from "hooks/useBranding";
import { FC, ReactElement, useEffect, useState } from "react";
import { useQuery } from "react-query";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { fetch } from "service/http";
import { removeAuthToken } from "utils/authStorage";
import { updateThemeColor } from "utils/themeColor";
import { BrandMark } from "./BrandMark";
import { BrandingControls } from "./BrandingControls";
import { AdminCapabilities } from "types/Admin";

const iconProps = { baseStyle: { w: 4, h: 4, flexShrink: 0 } };
const CoreSettingsIcon = chakra(Cog6ToothIcon, iconProps);
const LogoutIcon = chakra(ArrowLeftOnRectangleIcon, iconProps);
const HostsIcon = chakra(LinkIcon, iconProps);
const NodesIcon = chakra(SquaresPlusIcon, iconProps);
const NodesUsageIcon = chakra(ChartPieIcon, iconProps);
const ResetUsageIcon = chakra(DocumentMinusIcon, iconProps);
const UsersNavIcon = chakra(UsersIcon, iconProps);
const AdminsNavIcon = chakra(UserGroupIcon, iconProps);
const AuditNavIcon = chakra(ClipboardDocumentListIcon, iconProps);
const DeviceLimitNavIcon = chakra(ShieldCheckIcon, iconProps);
const PlansNavIcon = chakra(RectangleStackIcon, iconProps);

type ActionButtonProps = {
  icon: ReactElement;
  label: string;
  onClick: () => void;
  danger?: boolean;
};

const ActionButton: FC<ActionButtonProps> = ({ icon, label, onClick, danger }) => (
  <Button
    size="sm"
    variant="ghost"
    leftIcon={icon}
    justifyContent="flex-start"
    minW={0}
    w="full"
    color={danger ? "red.200" : "gray.200"}
    fontWeight="500"
    _hover={{ bg: danger ? "rgba(239, 68, 68, .14)" : "whiteAlpha.100", color: danger ? "red.100" : "white" }}
    _active={{ bg: danger ? "rgba(239, 68, 68, .2)" : "whiteAlpha.200" }}
    onClick={onClick}
  >
    <Text as="span" noOfLines={1}>{label}</Text>
  </Button>
);

export const Header: FC = () => {
  const { userData, getUserIsSuccess, getUserIsPending } = useGetUser();
  const { branding } = useBranding();
  const { colorMode, toggleColorMode } = useColorMode();
  const isOwner = !getUserIsPending && getUserIsSuccess && (userData.is_sudo || userData.role === "OWNER");
  const capabilities = useQuery<AdminCapabilities, Error>(
    ["admin-capabilities", userData.username],
    () => fetch("/admin/capabilities"),
    { enabled: getUserIsSuccess }
  );
  const canManage = Boolean(capabilities.data?.can_manage_admins);
  const { onEditingHosts, onResetAllUsage, onEditingNodes, onShowingNodesUsage } = useDashboard();
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  useEffect(() => {
    document.documentElement.dataset.panelTheme = userData.dashboard_theme || "heisenberg";
  }, [userData.dashboard_theme]);
  useEffect(() => updateThemeColor(colorMode), [colorMode]);
  useEffect(() => { setMobileMenuOpen(false); }, [location.pathname]);
  const isAdminsPage = location.pathname.startsWith("/admins");
  const isAuditPage = location.pathname.startsWith("/audit-logs");
  const isDeviceLimitPage = location.pathname.startsWith("/device-limits");
  const isPlansPage = location.pathname.startsWith("/plans");
  const isSettingsPage = location.pathname.startsWith("/settings");
  const isUsersPage = !isAdminsPage && !isAuditPage && !isDeviceLimitPage && !isPlansPage && !isSettingsPage;
  const logout = async () => {
    try {
      await fetch("/admin/logout", { method: "POST" });
    } finally {
      removeAuthToken();
      resetDashboardState();
      navigate("/login/");
    }
  };
  return (
    <Flex
      as="aside"
      w={{ base: "full", lg: "272px" }}
      minW={{ lg: "272px" }}
      minH={{ lg: "100vh" }}
      h={{ lg: "100vh" }}
      position={{ base: "relative", lg: "sticky" }}
      top="0"
      zIndex="sticky"
      direction="column"
      bg="rgba(11, 16, 32, .98)"
      color="white"
      borderEndWidth={{ lg: "1px" }}
      borderBottomWidth={{ base: "1px", lg: "0" }}
      borderColor="var(--panel-border)"
      backdropFilter="blur(16px)"
      px={{ base: 4, lg: 4 }}
      py={{ base: 3, lg: 5 }}
    >
      <HStack justify="space-between" align="center" gap={3}>
        <HStack spacing={3} minW={0}>
          {branding.logo_url || userData.logo_url
            ? <Image src={branding.logo_url || userData.logo_url || undefined} alt={`${branding.panel_name} logo`} boxSize={{ base: "38px", lg: "46px" }} objectFit="contain" borderRadius="10px" />
            : <BrandMark aria-hidden="true" boxSize={{ base: "38px", lg: "46px" }} filter="drop-shadow(0 8px 20px var(--panel-glow))" />}
          <Box minW={0}>
            <Text fontSize="sm" fontWeight="800" letterSpacing="-0.01em" color="white" noOfLines={1}>{branding.panel_name}</Text>
            <Text fontSize="xs" color="gray.400" mt="1px" noOfLines={1}>Operations workspace</Text>
          </Box>
        </HStack>
        <HStack display={{ base: "flex", lg: "none" }} spacing={1} flexShrink={0}>
          <IconButton onClick={toggleColorMode} size="sm" variant="ghost" aria-label={colorMode === "dark" ? "Use light theme" : "Use dark theme"} icon={colorMode === "dark" ? <SunIcon width={19} /> : <MoonIcon width={19} />} />
          <IconButton onClick={() => setMobileMenuOpen((value) => !value)} size="sm" variant="outline" aria-label={mobileMenuOpen ? "بستن منو" : "بازکردن منو"} aria-expanded={mobileMenuOpen} icon={mobileMenuOpen ? <XMarkIcon width={20} /> : <Bars3Icon width={20} />} />
        </HStack>
      </HStack>

      <Text display={{ base: "none", lg: "block" }} mt={8} mb={2} px={2} fontSize="xs" color="gray.500">ناوبری</Text>
      <Collapse in={mobileMenuOpen} animateOpacity style={{ overflow: "visible" }}>
      <SimpleGrid as="nav" aria-label="ناوبری اصلی" display={{ base: "grid", lg: "none" }} columns={{ base: 2, sm: 4 }} spacing={2} mt={4}>
        <Button
          as={Link}
          to="/"
          size="md"
          variant={isUsersPage ? "solid" : "ghost"}
          colorScheme={isUsersPage ? "primary" : "gray"}
          color={isUsersPage ? "#07130e" : "gray.200"}
          _hover={isUsersPage ? undefined : { bg: "whiteAlpha.100", color: "white" }}
          leftIcon={<UsersNavIcon />}
          justifyContent="flex-start"
          aria-current={isUsersPage ? "page" : undefined}
        >{t("users")}</Button>
        <Button
          hidden={!isOwner}
          as={Link}
          to="/plans/"
          size="md"
          variant={isPlansPage ? "solid" : "ghost"}
          colorScheme={isPlansPage ? "primary" : "gray"}
          color={isPlansPage ? "#07130e" : "gray.200"}
          _hover={isPlansPage ? undefined : { bg: "whiteAlpha.100", color: "white" }}
          leftIcon={<PlansNavIcon />}
          justifyContent="flex-start"
          aria-current={isPlansPage ? "page" : undefined}
        >پلن‌ها</Button>
        {canManage && (
          <Button
            as={Link}
            to="/admins/"
            size="md"
            variant={isAdminsPage ? "solid" : "ghost"}
            colorScheme={isAdminsPage ? "primary" : "gray"}
            color={isAdminsPage ? "#07130e" : "gray.200"}
            _hover={isAdminsPage ? undefined : { bg: "whiteAlpha.100", color: "white" }}
            leftIcon={<AdminsNavIcon />}
            justifyContent="flex-start"
            aria-current={isAdminsPage ? "page" : undefined}
          >{t("admins.nav")}</Button>
        )}
        {isOwner && (
          <Button
            as={Link}
            to="/device-limits/"
            size="md"
            variant={isDeviceLimitPage ? "solid" : "ghost"}
            colorScheme={isDeviceLimitPage ? "primary" : "gray"}
            color={isDeviceLimitPage ? "#07130e" : "gray.200"}
            _hover={isDeviceLimitPage ? undefined : { bg: "whiteAlpha.100", color: "white" }}
            leftIcon={<DeviceLimitNavIcon />}
            justifyContent="flex-start"
            aria-current={isDeviceLimitPage ? "page" : undefined}
          >{t("deviceLimit.nav")}</Button>
        )}
        {isOwner && <Button as={Link} to="/settings/" size="md" variant={isSettingsPage ? "solid" : "ghost"} colorScheme={isSettingsPage ? "primary" : "gray"} leftIcon={<CoreSettingsIcon />} justifyContent="flex-start" aria-current={isSettingsPage ? "page" : undefined}>Settings</Button>}
        {(
          <Button
            as={Link}
            to="/audit-logs/"
            size="md"
            variant={isAuditPage ? "solid" : "ghost"}
            colorScheme={isAuditPage ? "cyan" : "gray"}
            color={isAuditPage ? "#06161a" : "gray.200"}
            _hover={isAuditPage ? undefined : { bg: "whiteAlpha.100", color: "white" }}
            leftIcon={<AuditNavIcon />}
            justifyContent="flex-start"
            aria-current={isAuditPage ? "page" : undefined}
          >{t("audit.nav")}</Button>
        )}
      </SimpleGrid>
      </Collapse>

      <SimpleGrid as="nav" aria-label="ناوبری اصلی دسکتاپ" display={{ base: "none", lg: "grid" }} columns={1} spacing={2}>
        <Button as={Link} to="/" size="md" variant={isUsersPage ? "solid" : "ghost"} colorScheme={isUsersPage ? "primary" : "gray"} color={isUsersPage ? "#07130e" : "gray.200"} leftIcon={<UsersNavIcon />} justifyContent="flex-start" aria-current={isUsersPage ? "page" : undefined}>{t("users")}</Button>
        <Button hidden={!isOwner} as={Link} to="/plans/" size="md" variant={isPlansPage ? "solid" : "ghost"} colorScheme={isPlansPage ? "primary" : "gray"} color={isPlansPage ? "#08111f" : "gray.200"} leftIcon={<PlansNavIcon />} justifyContent="flex-start" aria-current={isPlansPage ? "page" : undefined}>Plans</Button>
        {canManage && <Button as={Link} to="/admins/" size="md" variant={isAdminsPage ? "solid" : "ghost"} colorScheme={isAdminsPage ? "primary" : "gray"} color={isAdminsPage ? "#07130e" : "gray.200"} leftIcon={<AdminsNavIcon />} justifyContent="flex-start" aria-current={isAdminsPage ? "page" : undefined}>{t("admins.nav")}</Button>}
        {isOwner && <Button as={Link} to="/device-limits/" size="md" variant={isDeviceLimitPage ? "solid" : "ghost"} colorScheme={isDeviceLimitPage ? "primary" : "gray"} color={isDeviceLimitPage ? "#07130e" : "gray.200"} leftIcon={<DeviceLimitNavIcon />} justifyContent="flex-start" aria-current={isDeviceLimitPage ? "page" : undefined}>{t("deviceLimit.nav")}</Button>}
        <Button as={Link} to="/audit-logs/" size="md" variant={isAuditPage ? "solid" : "ghost"} colorScheme={isAuditPage ? "cyan" : "gray"} color={isAuditPage ? "#06161a" : "gray.200"} leftIcon={<AuditNavIcon />} justifyContent="flex-start" aria-current={isAuditPage ? "page" : undefined}>{t("audit.nav")}</Button>
        {isOwner && <Button as={Link} to="/settings/" size="md" variant={isSettingsPage ? "solid" : "ghost"} colorScheme={isSettingsPage ? "primary" : "gray"} leftIcon={<CoreSettingsIcon />} justifyContent="flex-start" aria-current={isSettingsPage ? "page" : undefined}>Settings</Button>}
      </SimpleGrid>

      {isOwner && (
        <Box display={{ base: mobileMenuOpen ? "block" : "none", lg: "block" }} mt={{ base: 4, lg: 7 }} pt={{ base: 4, lg: 0 }} borderTopWidth={{ base: "1px", lg: "0" }} borderColor="whiteAlpha.200">
          <Text mb={2} px={2} fontSize="xs" color="gray.500" fontFamily="mono" letterSpacing=".1em" textTransform="uppercase">{t("core.configuration")}</Text>
          <SimpleGrid columns={{ base: 2, sm: 3, lg: 1 }} spacing={1}>
            <ActionButton icon={<CoreSettingsIcon />} label={t("core.title")} onClick={() => useDashboard.setState({ isEditingCore: true })} />
            <ActionButton icon={<HostsIcon />} label={t("header.hostSettings")} onClick={() => onEditingHosts(true)} />
            <ActionButton icon={<NodesIcon />} label={t("header.nodeSettings")} onClick={() => onEditingNodes(true)} />
            <ActionButton icon={<NodesUsageIcon />} label={t("header.nodesUsage")} onClick={() => onShowingNodesUsage(true)} />
            <ActionButton icon={<ResetUsageIcon />} label={t("resetAllUsage")} onClick={() => onResetAllUsage(true)} danger />
          </SimpleGrid>
        </Box>
      )}

      <Button display={{ base: mobileMenuOpen ? "flex" : "none", lg: "none" }} mt={3} onClick={logout} size="sm" variant="ghost" color="red.200" leftIcon={<LogoutIcon />}>{t("header.logout")}</Button>

      <Spacer display={{ base: "none", lg: "block" }} />
      <Stack display={{ base: "none", lg: "flex" }} mt={6} pt={4} borderTopWidth="1px" borderColor="whiteAlpha.200" spacing={2}>
        <Text fontSize="xs" color="gray.400" px={2} noOfLines={1}>{userData?.username || "Administrator"}</Text>
        <BrandingControls theme={userData.dashboard_theme || "heisenberg"} hasLogo={Boolean(userData.logo_url)} />
        <Button onClick={toggleColorMode} size="sm" variant="ghost" leftIcon={colorMode === "dark" ? <SunIcon width={16} /> : <MoonIcon width={16} />} justifyContent="flex-start">{colorMode === "dark" ? "Light theme" : "Dark theme"}</Button>
        <Button onClick={logout} size="sm" variant="ghost" color="red.200" leftIcon={<LogoutIcon />} justifyContent="flex-start" _hover={{ bg: "rgba(239, 68, 68, .14)", color: "red.100" }}>{t("header.logout")}</Button>
      </Stack>
    </Flex>
  );
};
