import {
  Badge,
  Box,
  BoxProps,
  Button,
  Card,
  chakra,
  Checkbox,
  CircularProgress,
  CircularProgressLabel,
  Divider,
  Flex,
  FormControl,
  FormLabel,
  Grid,
  HStack,
  IconButton,
  Popover,
  PopoverArrow,
  PopoverBody,
  PopoverCloseButton,
  PopoverContent,
  PopoverHeader,
  PopoverTrigger,
  Portal,
  Select,
  Stack,
  Text,
  Tooltip,
  Modal,
  ModalBody,
  ModalCloseButton,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalOverlay,
  useDisclosure,
  usePrefersReducedMotion,
  useToast,
  VStack,
} from "@chakra-ui/react";
import {
  ArrowPathIcon,
  CalendarDaysIcon,
  CheckIcon,
  ClipboardIcon,
  DevicePhoneMobileIcon,
  LinkIcon,
  PencilIcon,
  QrCodeIcon,
  RectangleStackIcon,
  UserCircleIcon,
  UsersIcon,
} from "@heroicons/react/24/outline";
import { ReactComponent as AddFileIcon } from "assets/add_file.svg";
import { resetStrategy, statusColors } from "constants/UserSettings";
import { useDashboard } from "contexts/DashboardContext";
import { FC, ReactNode, useEffect, useRef, useState } from "react";
import CopyToClipboard from "react-copy-to-clipboard";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "react-query";
import { Link } from "react-router-dom";
import { fetch } from "service/http";
import { AccountSummary, UserPlan } from "types/Admin";
import { User } from "types/User";
import { formatBytes } from "utils/formatByte";
import { localizedApiError } from "utils/apiError";
import useGetUser from "hooks/useGetUser";
import { BulkUserActions } from "./BulkUserActions";
import { CreateUserFromPlan } from "./CreateUserFromPlan";
import { OnlineBadge } from "./OnlineBadge";
import { Pagination } from "./Pagination";
import { UserDeviceLimit } from "./UserDeviceLimit";

const EmptySectionIcon = chakra(AddFileIcon);
const iconProps = { baseStyle: { w: 4, h: 4 } };
const CopyIcon = chakra(ClipboardIcon, iconProps);
const CopiedIcon = chakra(CheckIcon, iconProps);
const SubscriptionLinkIcon = chakra(LinkIcon, iconProps);
const QRIcon = chakra(QrCodeIcon, iconProps);
const EditIcon = chakra(PencilIcon, iconProps);
const ExpirationIcon = chakra(CalendarDaysIcon, iconProps);
const AccountIcon = chakra(UserCircleIcon, iconProps);
const SubscriptionIcon = chakra(ArrowPathIcon, iconProps);
const DeviceIcon = chakra(DevicePhoneMobileIcon, iconProps);
const PlanIcon = chakra(RectangleStackIcon, iconProps);
const LimitIcon = chakra(UsersIcon, iconProps);

type VisualState = "active" | "pending" | "danger";

const visualStatePalette: Record<
  VisualState,
  {
    accent: string;
    border: string;
    glow: string;
    tint: string;
    soft: string;
    text: string;
  }
> = {
  active: {
    accent: "#22c55e",
    border: "rgba(34, 197, 94, .58)",
    glow: "rgba(34, 197, 94, .17)",
    tint: "rgba(34, 197, 94, .12)",
    soft: "rgba(34, 197, 94, .08)",
    text: "green.200",
  },
  pending: {
    accent: "#eab308",
    border: "rgba(234, 179, 8, .62)",
    glow: "rgba(234, 179, 8, .18)",
    tint: "rgba(234, 179, 8, .12)",
    soft: "rgba(234, 179, 8, .075)",
    text: "yellow.200",
  },
  danger: {
    accent: "#ef4444",
    border: "rgba(239, 68, 68, .62)",
    glow: "rgba(239, 68, 68, .18)",
    tint: "rgba(239, 68, 68, .12)",
    soft: "rgba(239, 68, 68, .075)",
    text: "red.200",
  },
};

const dangerStatuses = new Set<User["status"]>([
  "disabled",
  "expired",
  "limited",
  "error",
]);

const getVisualState = (user: User): VisualState => {
  if (dangerStatuses.has(user.status)) return "danger";
  if (!user.online_at || user.status === "on_hold" || user.status === "connecting") {
    return "pending";
  }
  return "active";
};

const getResetStrategy = (strategy: string): string => {
  const entry = resetStrategy.find((item) => item.value === strategy);
  return entry?.title || "No";
};

const normalizeDate = (value: string) =>
  /(?:Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;

const formatDateTime = (value: string | null | undefined, locale: string) => {
  if (!value) return "—";
  const date = new Date(normalizeDate(value));
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
};

const formatTimestamp = (value: number | null | undefined, locale: string) => {
  if (!value) return "—";
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(
    new Date(value * 1000)
  );
};

const MetaLabel: FC<{ children: ReactNode }> = ({ children }) => (
  <Text
    color="rgba(203, 213, 225, .72)"
    fontSize="xs"
    fontWeight="700"
    lineHeight="1.45"
    letterSpacing=".01em"
  >
    {children}
  </Text>
);

type UsageRingProps = {
  user: User;
  visualState: VisualState;
};

const UsageRing: FC<UsageRingProps> = ({ user, visualState }) => {
  const { t } = useTranslation();
  const visual = visualStatePalette[visualState];
  const usedTraffic = user.used_traffic ?? 0;
  const isUnlimited = !user.data_limit;
  const percent = isUnlimited
    ? 100
    : Math.min(
        Math.max((usedTraffic / Math.max(user.data_limit || 1, 1)) * 100, 0),
        100
      );
  const displayValue = isUnlimited ? "∞" : `${Math.round(percent)}%`;
  const limitText = user.data_limit
    ? formatBytes(user.data_limit)
    : t("unlimited");

  return (
    <VStack spacing={1.5} flexShrink={0} minW="74px">
      <CircularProgress
        value={percent}
        size="68px"
        thickness="8px"
        color={visual.accent}
        trackColor="rgba(148, 163, 184, .12)"
        capIsRound
        aria-label={`${formatBytes(usedTraffic)} / ${limitText}`}
        sx={{
          "svg circle:last-of-type": {
            filter: `drop-shadow(0 0 5px ${visual.glow})`,
            transition: "stroke-dashoffset 220ms ease",
          },
          "@media (prefers-reduced-motion: reduce)": {
            "svg circle:last-of-type": { transition: "none" },
          },
        }}
      >
        <CircularProgressLabel
          dir="ltr"
          fontFamily="mono"
          fontSize="sm"
          fontWeight="900"
          color="gray.50"
        >
          {displayValue}
        </CircularProgressLabel>
      </CircularProgress>
      <MetaLabel>{t("usersTable.dataUsage")}</MetaLabel>
    </VStack>
  );
};

const GlowStatusBadge: FC<{ user: User; visualState: VisualState }> = ({
  user,
  visualState,
}) => {
  const { t } = useTranslation();
  const visual = visualStatePalette[visualState];
  const StatusIcon = statusColors[user.status].icon;

  return (
    <Badge
      display="inline-flex"
      alignItems="center"
      gap={1.5}
      maxW="full"
      px={2.5}
      py={1}
      borderRadius="full"
      bg={visual.tint}
      borderWidth="1px"
      borderColor={visual.border}
      color={visual.text}
      textTransform="none"
      whiteSpace="nowrap"
      boxShadow={`0 0 14px ${visual.glow}`}
    >
      <StatusIcon w={3.5} h={3.5} flexShrink={0} aria-hidden="true" />
      <Text as="span" fontSize="xs" fontWeight="800" lineHeight="1">
        {t(`status.${user.status}`)}
      </Text>
    </Badge>
  );
};

type InfoCellProps = {
  label: ReactNode;
  icon: ReactNode;
  children: ReactNode;
};

const InfoCell: FC<InfoCellProps> = ({ label, icon, children }) => (
  <Box
    minW={0}
    minH="72px"
    px={2.5}
    py={2.25}
    borderRadius="10px"
    bg="rgba(2, 6, 23, .38)"
    borderWidth="1px"
    borderColor="rgba(148, 163, 184, .13)"
    boxShadow="inset 0 1px 0 rgba(255,255,255,.018)"
  >
    <HStack spacing={1.5} minW={0} color="rgba(203, 213, 225, .72)">
      <Box display="inline-flex" flexShrink={0} aria-hidden="true">
        {icon}
      </Box>
      <MetaLabel>{label}</MetaLabel>
    </HStack>
    <Box mt={1.5} minW={0} color="gray.100">
      {children}
    </Box>
  </Box>
);

const NextPlanSummary: FC<{ user: User }> = ({ user }) => {
  const { t, i18n } = useTranslation();
  const plan = user.next_plan;

  if (!plan) {
    return (
      <Text color="gray.500" fontSize="xs" lineHeight="1.55" noOfLines={2}>
        {t("usersTable.noNextPlan")}
      </Text>
    );
  }

  return (
    <Stack spacing={0.75} minW={0}>
      <Text
        dir="ltr"
        textAlign="start"
        fontFamily="mono"
        fontSize="xs"
        fontWeight="800"
        color="gray.100"
        noOfLines={1}
        sx={{ unicodeBidi: "isolate" }}
      >
        {plan.data_limit ? formatBytes(plan.data_limit) : t("unlimited")}
      </Text>
      <Text
        dir="ltr"
        textAlign="start"
        color="gray.400"
        fontSize="10px"
        noOfLines={1}
        sx={{ unicodeBidi: "isolate" }}
      >
        {formatTimestamp(plan.expire, i18n.language)}
      </Text>
    </Stack>
  );
};

const ResetHistory: FC<{ user: User }> = ({ user }) => {
  const { t, i18n } = useTranslation();
  const history = [...(user.reset_history || [])].sort(
    (a, b) => new Date(b.reset_at).getTime() - new Date(a.reset_at).getTime()
  );

  if (history.length === 0) {
    return (
      <Text color="gray.500" fontSize="xs" lineHeight="1.55" noOfLines={2}>
        {t("usersTable.noResetHistory")}
      </Text>
    );
  }

  return (
    <Popover isLazy placement="auto" strategy="fixed">
      <PopoverTrigger>
        <Button
          variant="link"
          color="green.200"
          minH="32px"
          h="auto"
          fontSize="xs"
          fontWeight="800"
          whiteSpace="normal"
          textAlign="start"
          onClick={(event) => event.stopPropagation()}
          _focusVisible={{ outline: "2px solid", outlineColor: "green.300" }}
        >
          {t("usersTable.resetCount", { count: history.length })}
        </Button>
      </PopoverTrigger>
      <Portal>
        <PopoverContent
          dir={i18n.dir()}
          bg="#080f19"
          color="gray.100"
          borderColor="rgba(34, 197, 94, .38)"
          boxShadow="0 18px 46px rgba(0,0,0,.52)"
          maxW={{ base: "calc(100vw - 24px)", sm: "360px" }}
          onClick={(event) => event.stopPropagation()}
        >
          <PopoverArrow bg="#080f19" />
          <PopoverCloseButton />
          <PopoverHeader fontWeight="800" borderColor="whiteAlpha.100" pe={10}>
            {t("usersTable.resetHistory")}
          </PopoverHeader>
          <PopoverBody maxH="280px" overflowY="auto" p={0}>
            {history.map((item, index) => (
              <Box key={`${item.reset_at}-${index}`} px={4} py={3}>
                <HStack justify="space-between" align="start" gap={4}>
                  <Text
                    fontSize="sm"
                    fontFamily="mono"
                    dir="ltr"
                    sx={{ unicodeBidi: "isolate" }}
                  >
                    {formatBytes(item.used_traffic)}
                  </Text>
                  <Text fontSize="xs" color="gray.400" textAlign="end">
                    {formatDateTime(item.reset_at, i18n.language)}
                  </Text>
                </HStack>
                {index < history.length - 1 && (
                  <Divider mt={3} borderColor="whiteAlpha.100" />
                )}
              </Box>
            ))}
          </PopoverBody>
        </PopoverContent>
      </Portal>
    </Popover>
  );
};

type ActionButtonsProps = {
  user: User;
  onEdit: () => void;
  onRenew: () => void;
  accent: string;
  readOnly: boolean;
};

const ActionButtons: FC<ActionButtonsProps> = ({ user, onEdit, onRenew, accent, readOnly }) => {
  const { setQRCode, setSubLink } = useDashboard();
  const { t } = useTranslation();
  const proxyLinks = user.links.join("\r\n");
  const [copied, setCopied] = useState<[number, boolean]>([-1, false]);

  useEffect(() => {
    if (!copied[1]) return;
    const timer = window.setTimeout(() => setCopied([-1, false]), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const buttonStyle = {
    variant: "ghost",
    size: "sm",
    minW: "44px",
    h: "44px",
    color: "gray.300",
    borderRadius: "9px",
    borderWidth: "1px",
    borderColor: "rgba(148, 163, 184, .16)",
    bg: "rgba(2, 6, 23, .28)",
    _hover: {
      bg: "rgba(255, 255, 255, .07)",
      color: accent,
      borderColor: accent,
    },
    _focusVisible: {
      outline: "2px solid",
      outlineColor: accent,
      outlineOffset: "2px",
    },
    transition: "background-color 160ms ease, border-color 160ms ease, color 160ms ease",
  } as const;

  return (
    <HStack
      dir="ltr"
      justify="flex-start"
      spacing={1.5}
      flexWrap="wrap"
      onClick={(event) => event.stopPropagation()}
    >
      <CopyToClipboard
        text={
          user.subscription_url.startsWith("/")
            ? window.location.origin + user.subscription_url
            : user.subscription_url
        }
        onCopy={() => setCopied([0, true])}
      >
        <Box>
          <Tooltip
            label={
              copied[0] === 0 && copied[1]
                ? t("usersTable.copied")
                : t("usersTable.copyLink")
            }
            placement="top"
          >
            <IconButton
              {...buttonStyle}
              aria-label={t("usersTable.copyLink")}
              icon={copied[0] === 0 && copied[1] ? <CopiedIcon /> : <SubscriptionLinkIcon />}
            />
          </Tooltip>
        </Box>
      </CopyToClipboard>
      <CopyToClipboard text={proxyLinks} onCopy={() => setCopied([1, true])}>
        <Box>
          <Tooltip
            label={
              copied[0] === 1 && copied[1]
                ? t("usersTable.copied")
                : t("usersTable.copyConfigs")
            }
            placement="top"
          >
            <IconButton
              {...buttonStyle}
              aria-label={t("usersTable.copyConfigs")}
              icon={copied[0] === 1 && copied[1] ? <CopiedIcon /> : <CopyIcon />}
            />
          </Tooltip>
        </Box>
      </CopyToClipboard>
      <Tooltip label={t("usersTable.qrCode")} placement="top">
        <IconButton
          {...buttonStyle}
          aria-label={t("usersTable.qrCode")}
          icon={<QRIcon />}
          onClick={() => {
            setQRCode(user.links);
            setSubLink(user.subscription_url);
          }}
        />
      </Tooltip>
      {!readOnly && <Tooltip label={t("userDialog.editUser")} placement="top">
        <IconButton
          {...buttonStyle}
          aria-label={t("userDialog.editUser")}
          icon={<EditIcon />}
          onClick={onEdit}
        />
      </Tooltip>}
      {!readOnly && <Tooltip label="تمدید با پلن" placement="top">
        <IconButton
          {...buttonStyle}
          aria-label="تمدید با پلن"
          icon={<PlanIcon />}
          onClick={onRenew}
        />
      </Tooltip>}
    </HStack>
  );
};

type UserCardProps = {
  user: User;
  selected: boolean;
  onSelectedChange: (selected: boolean) => void;
  onOpen: () => void;
  onRenew: () => void;
  readOnly: boolean;
  isOwner: boolean;
};

const UserCard: FC<UserCardProps> = ({
  user,
  selected,
  onSelectedChange,
  onOpen,
  onRenew,
  readOnly,
  isOwner,
}) => {
  const { t, i18n } = useTranslation();
  const reduceMotion = usePrefersReducedMotion();
  const visualState = getVisualState(user);
  const visual = visualStatePalette[visualState];
  const owner = user.admin?.username || "—";
  const trafficLimit = user.data_limit
    ? formatBytes(user.data_limit)
    : t("unlimited");
  const resetText = t(
    `userDialog.resetStrategy${getResetStrategy(
      user.data_limit_reset_strategy || "no_reset"
    )}`
  );

  return (
    <Card
      as="article"
      dir={i18n.dir()}
      minW={0}
      h="full"
      overflow="hidden"
      position="relative"
      borderRadius="14px"
      borderWidth="1px"
      borderColor={selected ? visual.accent : visual.border}
      bg="rgba(3, 9, 17, .9)"
      backgroundImage={`radial-gradient(circle at 14% 0%, ${visual.tint}, transparent 38%), linear-gradient(145deg, rgba(8, 19, 28, .96), rgba(2, 7, 13, .97))`}
      backdropFilter="blur(18px)"
      boxShadow={`0 10px 26px rgba(0, 0, 0, .28), 0 0 20px ${visual.glow}`}
      transition={
        reduceMotion ? "none" : "border-color 180ms ease, box-shadow 180ms ease"
      }
      _hover={{
        borderColor: visual.accent,
        boxShadow: `0 14px 34px rgba(0, 0, 0, .36), 0 0 24px ${visual.glow}`,
      }}
      sx={{
        "@media (prefers-reduced-motion: reduce)": { transition: "none" },
      }}
    >
      <Box
        aria-hidden="true"
        position="absolute"
        insetInline={0}
        top={0}
        h="2px"
        bg={`linear-gradient(90deg, transparent, ${visual.accent}, transparent)`}
        opacity={selected ? 1 : 0.76}
      />

      <Stack spacing={3} p={{ base: 3.5, md: 3 }} pt={{ base: 4, md: 3.5 }} h="full">
        <HStack align="start" justify="space-between" spacing={3} minW={0}>
          {user.used_traffic !== null && <UsageRing user={user} visualState={visualState} />}

          <Stack flex="1" minW={0} spacing={2} align="stretch">
            <HStack justify="space-between" align="start" gap={2} minW={0}>
              <Box minW={0} flex="1">
                <HStack spacing={2} minW={0}>
                  <OnlineBadge lastOnline={user.online_at} />
                  <Text
                    dir="ltr"
                    textAlign="start"
                    fontFamily="mono"
                    fontSize={{ base: "md", md: "sm" }}
                    fontWeight="900"
                    letterSpacing="-.015em"
                    color="gray.50"
                    noOfLines={1}
                    minW={0}
                    sx={{ unicodeBidi: "isolate" }}
                  >
                    {user.username}
                  </Text>
                </HStack>
                <Box mt={1.5}>
                  <GlowStatusBadge user={user} visualState={visualState} />
                </Box>
              </Box>
              {!readOnly && <Checkbox
                isChecked={selected}
                onChange={(event) => onSelectedChange(event.target.checked)}
                colorScheme={visualState === "danger" ? "red" : visualState === "pending" ? "yellow" : "green"}
                size="lg"
                flexShrink={0}
                aria-label={`${t("usersTable.selectUser")}: ${user.username}`}
              />}
            </HStack>

            <HStack spacing={2} flexWrap="wrap" minW={0}>
              {user.used_traffic !== null && <Text
                dir="ltr"
                textAlign="start"
                fontFamily="mono"
                fontSize="xs"
                fontWeight="800"
                color="gray.100"
                noOfLines={1}
                sx={{ unicodeBidi: "isolate" }}
              >
                {formatBytes(user.used_traffic)} / {trafficLimit}
              </Text>}
              <Tooltip label={t("userDialog.concurrentUserLimit")} placement="top">
                <HStack
                  dir="ltr"
                  spacing={1}
                  px={2}
                  py={0.5}
                  borderRadius="full"
                  bg={visual.soft}
                  color={visual.text}
                  borderWidth="1px"
                  borderColor={visual.border}
                >
                  <LimitIcon aria-hidden="true" />
                  <Text fontFamily="mono" fontSize="xs" fontWeight="900">
                    {user.concurrent_user_limit ?? "∞"}
                  </Text>
                </HStack>
              </Tooltip>
            </HStack>
          </Stack>
        </HStack>

        <HStack
          justify="flex-start"
          gap={2}
          px={2.5}
          py={2}
          borderRadius="10px"
          bg="rgba(2, 6, 23, .36)"
          borderWidth="1px"
          borderColor="rgba(148, 163, 184, .12)"
          minW={0}
        >
          <HStack spacing={1.5} minW={0}>
            <ExpirationIcon color={visual.text} aria-hidden="true" />
            <Text color="gray.400" fontSize="10px" fontWeight="700" whiteSpace="nowrap">
              {t("usersTable.expiration")}
            </Text>
            <Text
              dir="ltr"
              textAlign="start"
              fontFamily={user.expire ? "mono" : "body"}
              fontSize="xs"
              fontWeight="800"
              color="gray.100"
              noOfLines={1}
              sx={{ unicodeBidi: "isolate" }}
            >
              {user.expire ? formatTimestamp(user.expire, i18n.language) : t("unlimited")}
            </Text>
          </HStack>
          <Badge
            flexShrink={0}
            px={2}
            py={0.5}
            borderRadius="full"
            bg="whiteAlpha.50"
            color="gray.300"
            textTransform="none"
            fontSize="10px"
            whiteSpace="nowrap"
          >
            {resetText}
          </Badge>
        </HStack>

        <Grid
          templateColumns="repeat(2, minmax(0, 1fr))"
          gap={2}
          minW={0}
          sx={{
            "@media screen and (max-width: 350px)": {
              gridTemplateColumns: "minmax(0, 1fr)",
            },
          }}
        >
          <InfoCell label={t("usersTable.admin")} icon={<AccountIcon />}>
            <Text
              dir="ltr"
              textAlign="start"
              fontFamily="mono"
              fontSize="xs"
              fontWeight="800"
              noOfLines={1}
              overflowWrap="anywhere"
              sx={{ unicodeBidi: "isolate" }}
            >
              {owner}
            </Text>
          </InfoCell>

          <InfoCell label={t("usersTable.createdAt")} icon={<ExpirationIcon />}>
            <Text fontSize="xs" lineHeight="1.55" noOfLines={2}>
              {formatDateTime(user.created_at, i18n.language)}
            </Text>
          </InfoCell>

          <InfoCell label={t("usersTable.subscriptionUpdatedAt")} icon={<SubscriptionIcon />}>
            <Text fontSize="xs" lineHeight="1.55" noOfLines={2}>
              {formatDateTime(user.sub_updated_at, i18n.language)}
            </Text>
          </InfoCell>

          <InfoCell label={t("usersTable.lastUserAgent")} icon={<DeviceIcon />}>
            <Tooltip label={user.sub_last_user_agent || "—"} placement="top" hasArrow>
              <Text
                dir="ltr"
                textAlign="start"
                fontSize="xs"
                lineHeight="1.55"
                noOfLines={2}
                overflowWrap="anywhere"
                sx={{ unicodeBidi: "isolate" }}
              >
                {user.sub_last_user_agent || "—"}
              </Text>
            </Tooltip>
          </InfoCell>

          <InfoCell label={t("usersTable.nextPlan")} icon={<PlanIcon />}>
            <NextPlanSummary user={user} />
          </InfoCell>

          <InfoCell label={t("usersTable.resetHistory")} icon={<SubscriptionIcon />}>
            <ResetHistory user={user} />
          </InfoCell>
        </Grid>

        <Flex
          mt="auto"
          pt={2.5}
          borderTopWidth="1px"
          borderColor="rgba(148, 163, 184, .13)"
          justify="space-between"
          align="center"
          gap={2}
          wrap="wrap"
        >
          <ActionButtons user={user} onEdit={onOpen} onRenew={onRenew} accent={visual.accent} readOnly={readOnly} />
          {!readOnly && isOwner && <UserDeviceLimit user={user} />}
        </Flex>
      </Stack>
    </Card>
  );
};

const EmptySection: FC<{ isFiltered: boolean; readOnly: boolean }> = ({ isFiltered, readOnly }) => {
  const [planCreateOpen, setPlanCreateOpen] = useState(false);
  const { onCreateUser } = useDashboard();
  const { t } = useTranslation();
  const account = useQuery<AccountSummary, Error>("account-summary", () => fetch("/account/summary"));
  const { userData, getUserIsSuccess } = useGetUser();
  const isOwner = getUserIsSuccess && (userData.is_sudo || userData.role === "OWNER");

  return (
    <VStack px={5} py={12} spacing={4} textAlign="center">
      <EmptySectionIcon
        maxH="150px"
        maxW="150px"
        aria-hidden="true"
        _dark={{
          'path[fill="#fff"]': { fill: "gray.800" },
          'path[fill="#f2f2f2"], path[fill="#e6e6e6"], path[fill="#ccc"]': {
            fill: "gray.700",
          },
          'circle[fill="#3182CE"]': { fill: "primary.300" },
        }}
      />
      <Text color="gray.300" maxW="52ch">
        {isFiltered ? t("usersTable.noUserMatched") : t("usersTable.noUser")}
      </Text>
      {!readOnly && !isFiltered && account.data?.billing_mode !== "USER_CREDIT" && ["FREE_FORM", "FORM_ONLY", "BOTH"].includes(account.data?.user_creation_mode || "") && (
        <Button size="sm" colorScheme="primary" onClick={() => onCreateUser(true)}>
          {t("createUser")}
        </Button>
      )}
      {!readOnly && !isFiltered && (account.data?.billing_mode === "USER_CREDIT" || ["PLAN_ONLY", "BOTH"].includes(account.data?.user_creation_mode || "")) && (
        <Button onClick={() => setPlanCreateOpen(true)} size="sm" colorScheme="primary">
          ساخت کاربر از پلن
        </Button>
      )}
      <CreateUserFromPlan isOpen={planCreateOpen} onClose={() => setPlanCreateOpen(false)} />
    </VStack>
  );
};

type UsersTableProps = BoxProps;

export const UsersTable: FC<UsersTableProps> = (props) => {
  const {
    filters,
    users: { users },
    onEditingUser,
    refetchUsers,
  } = useDashboard();
  const { i18n } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const renewalModal = useDisclosure();
  const account = useQuery<AccountSummary, Error>("account-summary", () => fetch("/account/summary"));
  const { userData, getUserIsSuccess } = useGetUser();
  const isOwner = getUserIsSuccess && (userData.is_sudo || userData.role === "OWNER");
  const readOnly = account.data?.account_status !== "ACTIVE";
  const [renewalUser, setRenewalUser] = useState<User | null>(null);
  const [renewalPlanId, setRenewalPlanId] = useState("");
  const renewalRequest = useRef<{ key: string; id: string } | null>(null);
  const plans = useQuery<UserPlan[], Error>(
    "user-plans",
    () => fetch("/user-plans"),
    { enabled: renewalModal.isOpen }
  );
  const renew = useMutation(
    ({ user, planId }: { user: User; planId: number }) => {
      const key = `${user.username}:${planId}`;
      if (renewalRequest.current?.key !== key) renewalRequest.current = { key, id: `renew-${crypto.randomUUID()}` };
      return fetch(`/users/${user.username}/renew-from-plan`, {
        method: "POST",
        body: {
          plan_id: planId,
          idempotency_key: renewalRequest.current.id,
        },
      });
    },
    {
      onSuccess: () => {
        refetchUsers();
        queryClient.invalidateQueries("account-summary");
        renewalModal.onClose();
        setRenewalUser(null);
        setRenewalPlanId("");
        toast({ title: "کاربر با پلن تمدید شد", status: "success", duration: 3000 });
      },
      onError: (error: any) => {
        toast({
          title: "تمدید انجام نشد",
          description: localizedApiError(error),
          status: "error",
          duration: 5000,
        });
      },
    }
  );
  const direction = i18n.dir();
  const isFiltered = Boolean(
    filters.search ||
      filters.status ||
      filters.admin ||
      filters.sort !== "-created_at"
  );
  const [selectedUsernames, setSelectedUsernames] = useState<Set<string>>(
    () => new Set()
  );
  const selectedUsers = users.filter((user) => selectedUsernames.has(user.username));
  const allVisibleSelected =
    users.length > 0 && users.every((user) => selectedUsernames.has(user.username));

  useEffect(() => {
    const visible = new Set(users.map((user) => user.username));
    setSelectedUsernames(
      (current) => new Set([...current].filter((username) => visible.has(username)))
    );
  }, [users]);

  const setUserSelected = (username: string, selected: boolean) => {
    setSelectedUsernames((current) => {
      const next = new Set(current);
      if (selected) next.add(username);
      else next.delete(username);
      return next;
    });
  };

  const toggleAllVisible = (selected: boolean) => {
    setSelectedUsernames(selected ? new Set(users.map((user) => user.username)) : new Set());
  };

  return (
    <Box
      {...props}
      id="users-table"
      dir={direction}
      w="full"
      maxW="full"
      minW={0}
      overflowX="hidden"
      borderTopWidth="1px"
      borderColor="rgba(148, 163, 184, .13)"
    >
      {users.length === 0 ? (
        <EmptySection isFiltered={isFiltered} readOnly={readOnly} />
      ) : (
        <>
          {!readOnly && <BulkUserActions
            users={selectedUsers}
            allVisibleSelected={allVisibleSelected}
            visibleCount={users.length}
            onToggleAll={toggleAllVisible}
            onClear={() => setSelectedUsernames(new Set())}
          />}
          <Grid
            templateColumns={{
              base: "minmax(0, 1fr)",
              md: "repeat(2, minmax(0, 1fr))",
              xl: "repeat(3, minmax(0, 1fr))",
              "2xl": "repeat(4, minmax(0, 1fr))",
            }}
            gap={{ base: 3, md: 3.5 }}
            w="full"
            maxW="full"
            minW={0}
            py={4}
            alignItems="stretch"
            sx={{
              "@media screen and (min-width: 1850px)": {
                gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
              },
            }}
          >
            {users.map((user) => (
              <UserCard
                key={user.username}
                user={user}
                selected={selectedUsernames.has(user.username)}
                onSelectedChange={(selected) => setUserSelected(user.username, selected)}
                readOnly={readOnly}
                isOwner={isOwner}
                onOpen={() => onEditingUser(user)}
                onRenew={() => {
                  renewalRequest.current = null;
                  setRenewalUser(user);
                  setRenewalPlanId("");
                  renewalModal.onOpen();
                }}
              />
            ))}
          </Grid>
        </>
      )}
      <Pagination />
      <Modal
        isOpen={renewalModal.isOpen}
        onClose={() => {
          if (renew.isLoading) return;
          renewalModal.onClose();
          setRenewalUser(null);
          setRenewalPlanId("");
        }}
        isCentered
      >
        <ModalOverlay bg="rgba(0,0,0,.72)" />
        <ModalContent mx={3} bg="#111d17" color="gray.100" borderWidth="1px" borderColor="#33483b">
          <ModalHeader>تمدید «{renewalUser?.username}» با پلن</ModalHeader>
          <ModalCloseButton isDisabled={renew.isLoading} />
          <ModalBody>
            <FormControl isRequired>
              <FormLabel>پلن قابل‌دسترسی</FormLabel>
              <Select
                minH="44px"
                value={renewalPlanId}
                onChange={(event) => setRenewalPlanId(event.target.value)}
                isDisabled={plans.isLoading || plans.isError || renew.isLoading || !!renewalRequest.current}
              >
                <option value="">انتخاب پلن</option>
                {(plans.data || []).map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.name} · v{plan.version_number} · {formatBytes(plan.version.data_limit)} · {plan.version.duration_days} روز
                  </option>
                ))}
              </Select>
              {renewalPlanId && <Text mt={2} fontSize="sm" role="status">قیمت پلن: {(plans.data?.find((plan) => plan.id === Number(renewalPlanId))?.effective_price_toman || 0).toLocaleString()} تومان؛ کسر اعتبار مطابق سیاست حساب است.</Text>}
              {plans.isError && <Text mt={2} color="red.300" fontSize="sm">دریافت پلن‌ها انجام نشد.</Text>}
              {!plans.isLoading && !plans.isError && (plans.data || []).length === 0 && (
                <Text mt={2} color="gray.400" fontSize="sm">پلن فعالی برای این حساب در دسترس نیست.</Text>
              )}
            </FormControl>
          </ModalBody>
          <ModalFooter gap={3}>
            <Button minH="44px" variant="ghost" isDisabled={renew.isLoading} onClick={renewalModal.onClose}>انصراف</Button>
            <Button
              minH="44px"
              colorScheme="primary"
              color="#07130e"
              isDisabled={!renewalUser || !renewalPlanId}
              isLoading={renew.isLoading}
              onClick={() => renewalUser && renewalPlanId && renew.mutate({ user: renewalUser, planId: Number(renewalPlanId) })}
            >
              تأیید تمدید
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </Box>
  );
};
