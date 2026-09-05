import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Card,
  Collapse,
  HStack,
  IconButton,
  Progress,
  SimpleGrid,
  Skeleton,
  Stack,
  Text,
  chakra,
  useBreakpointValue,
} from "@chakra-ui/react";
import {
  BoltIcon,
  ChartBarIcon,
  ClockIcon,
  CpuChipIcon,
  RectangleStackIcon,
  ServerStackIcon,
  SignalIcon,
  UserGroupIcon,
  UserPlusIcon,
  UsersIcon,
} from "@heroicons/react/24/outline";
import type { ApexOptions } from "apexcharts";
import { useDashboard } from "contexts/DashboardContext";
import useGetUser from "hooks/useGetUser";
import { FC, ReactElement, useMemo, useState } from "react";
import Chart from "react-apexcharts";
import { useQuery } from "react-query";
import { useNavigate } from "react-router-dom";
import { fetch } from "service/http";
import { AccountSummary, AdminCapabilities } from "types/Admin";
import { DashboardOverview as DashboardOverviewData } from "types/Dashboard";
import { formatBytes } from "utils/formatByte";

const iconStyle = { baseStyle: { w: 5, h: 5 } };
const UsersKpiIcon = chakra(UsersIcon, iconStyle);
const OnlineIcon = chakra(SignalIcon, iconStyle);
const NewUsersIcon = chakra(UserPlusIcon, iconStyle);
const CpuIcon = chakra(CpuChipIcon, iconStyle);
const ServerIcon = chakra(ServerStackIcon, iconStyle);
const TrafficIcon = chakra(ChartBarIcon, iconStyle);

const billingModeLabels: Record<string, string> = {
  LEGACY_COMPAT: "حالت قدیمی",
  SEAT_CREDIT: "ظرفیت دستگاه قدیمی",
  USED_TRAFFIC: "مصرف واقعی",
  ALLOCATED_TRAFFIC: "حجم ساخته‌شده",
  USER_CREDIT: "نامحدود · سقف اکانت",
};

const roleLabels: Record<string, string> = {
  OWNER: "مالک",
  ADMIN: "ادمین",
};

const activityLabels: Record<string, string> = {
  "auth.login": "وارد پنل شد",
  "auth.logout": "از پنل خارج شد",
  "admin.create": "ادمین ساخت",
  "admin.update": "ادمین را ویرایش کرد",
  "admin.delete": "ادمین را حذف کرد",
  "admin.activate": "ادمین را فعال کرد",
  "admin.owner_freeze": "شاخه ادمین را فریز کرد",
  "admin.owner_unfreeze": "فریز ادمین را برداشت",
  "admin.resume": "ادمین را از توقف خارج کرد",
  "user.create": "کاربر ساخت",
  "user.create_from_plan": "کاربر را از پلن ساخت",
  "user.update": "کاربر را ویرایش کرد",
  "user.delete": "کاربر را حذف کرد",
  "credit.grant": "اعتبار ادمین را افزایش داد",
  "credit.reclaim": "اعتبار ادمین را پس گرفت",
  "trial_quota.reset": "سهمیه تست را بازنشانی کرد",
};

type RecentActivity = {
  id: number;
  admin_username: string;
  action: string;
  target_name: string | null;
  target_id: string | null;
  status: string;
  created_at: string;
};

type SystemStats = {
  version: string;
  mem_total: number;
  mem_used: number;
  cpu_usage: number;
  incoming_bandwidth_speed: number;
  outgoing_bandwidth_speed: number;
};

type BackupArtifact = {
  period_key: string;
  generation_status: string;
  delivery_status: string;
};

const faNumber = (value: number) => value.toLocaleString("fa-IR");

const Kpi: FC<{ label: string; value: string; detail: string; icon: ReactElement; fullMobile?: boolean }> = ({ label, value, detail, icon, fullMobile = false }) => (
  <Card gridColumn={fullMobile ? { base: "span 2", sm: "auto" } : undefined} p={3.5} minH="118px" bg="var(--panel-surface)" color="inherit" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="14px">
    <HStack justify="space-between" align="start" gap={3}>
      <Box minW={0}>
        <Text color="gray.400" fontSize="xs" fontWeight="700">{label}</Text>
        <Text mt={1} fontSize="2xl" fontWeight="800" sx={{ fontVariantNumeric: "tabular-nums" }}>{value}</Text>
      </Box>
      <Box flexShrink={0} p={2} color="primary.300" bg="var(--panel-nested)" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px">{icon}</Box>
    </HStack>
    <Text mt={2} color="gray.400" fontSize="xs" lineHeight="1.6">{detail}</Text>
  </Card>
);

const QuickAction: FC<{ label: string; icon: ReactElement; onClick: () => void }> = ({ label, icon, onClick }) => (
  <Button minH="44px" h="auto" px={3} variant="outline" borderColor="var(--panel-border)" bg="var(--panel-surface)" gap={2} fontSize="xs" justifyContent="flex-start" _hover={{ borderColor: "primary.400", color: "primary.200" }} onClick={onClick}>
    <Box color="primary.300">{icon}</Box><Text>{label}</Text>
  </Button>
);

const ResourceMeter: FC<{ label: string; value: string; detail: string; percent?: number; icon: ReactElement }> = ({ label, value, detail, percent, icon }) => (
  <Box p={3} minW={0} bg="var(--panel-nested)" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="12px">
    <HStack justify="space-between" align="start" gap={3}>
      <Box minW={0}><Text color="gray.400" fontSize="xs">{label}</Text><Text mt={1} fontSize="lg" fontWeight="800" dir="ltr">{value}</Text></Box>
      <Box color="primary.300">{icon}</Box>
    </HStack>
    {percent !== undefined && <Progress mt={2.5} value={percent} size="sm" borderRadius="full" colorScheme={percent >= 85 ? "red" : percent >= 70 ? "orange" : "green"} aria-label={`${label}: ${Math.round(percent)} درصد`} />}
    <Text mt={2} color="gray.500" fontSize="xs">{detail}</Text>
  </Box>
);

const activityText = (activity: RecentActivity) => {
  const target = activity.target_name || activity.target_id;
  const action = activityLabels[activity.action] || activity.action.replaceAll(".", " / ");
  return `${activity.admin_username} ${action}${target ? ` · ${target}` : ""}`;
};

const activityTime = (value: string) => {
  const normalized = /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : `${value}Z`;
  return new Intl.DateTimeFormat("fa-IR", {
    timeZone: "Asia/Tehran",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    day: "numeric",
  }).format(new Date(normalized));
};

type Props = {
  onCreateAdmin: () => void;
  onCreatePlan: () => void;
};

export const DashboardOverview: FC<Props> = ({ onCreateAdmin, onCreatePlan }) => {
  const { userData, getUserIsPending, getUserIsSuccess } = useGetUser();
  const { version } = useDashboard();
  const navigate = useNavigate();
  const [quickOpen, setQuickOpen] = useState(false);
  const [mobileDetailsOpen, setMobileDetailsOpen] = useState(false);
  const desktopDetailsVisible = useBreakpointValue({ base: false, md: true }) ?? false;
  const timezoneOffset = -new Date().getTimezoneOffset();
  const query = useQuery<DashboardOverviewData, Error>(
    ["dashboard-overview", timezoneOffset],
    () => fetch(`/dashboard/overview?timezone_offset_minutes=${timezoneOffset}`),
    { enabled: getUserIsSuccess, refetchInterval: 30000 },
  );
  const capabilities = useQuery<AdminCapabilities, Error>(
    ["admin-capabilities", userData.username],
    () => fetch("/admin/capabilities"),
    { enabled: getUserIsSuccess },
  );
  const account = useQuery<AccountSummary, Error>(
    ["account-summary", userData.username],
    () => fetch("/account/summary"),
    { enabled: getUserIsSuccess, refetchInterval: 15000 },
  );
  const activity = useQuery<RecentActivity[], Error>(
    ["dashboard-activity", userData.username],
    () => fetch("/account/activity?limit=5"),
    { enabled: getUserIsSuccess, refetchInterval: 30000 },
  );
  const isOwner = account.data?.role === "OWNER" || userData.role === "OWNER" || userData.is_sudo;
  const system = useQuery<SystemStats, Error>(
    "statistics-query-key",
    () => fetch("/system"),
    {
      enabled: Boolean(isOwner && account.data),
      refetchInterval: 10000,
      onSuccess: ({ version: currentVersion }) => {
        if (version !== currentVersion) useDashboard.setState({ version: currentVersion });
      },
    },
  );
  const backups = useQuery<BackupArtifact[], Error>(
    "dashboard-backup-status",
    () => fetch("/owner/backups"),
    { enabled: Boolean(isOwner && account.data), refetchInterval: 60000 },
  );
  const data = query.data;
  const chartText = "#a8b0aa";
  const trafficModes = useMemo(
    () => (data?.billing_modes || []).filter((item) => item.current_used_traffic !== null && (item.current_used_traffic > 0 || item.allocated_quota > 0)),
    [data],
  );
  const barOptions = useMemo<ApexOptions>(() => ({
    chart: { toolbar: { show: false }, animations: { enabled: false }, background: "transparent", fontFamily: "Vazirmatn" },
    colors: ["#d7ad54", "#42b97b"],
    dataLabels: { enabled: false },
    grid: { borderColor: "rgba(148,163,184,.10)" },
    plotOptions: { bar: { borderRadius: 4, columnWidth: "48%" } },
    xaxis: {
      categories: trafficModes.map((item) => billingModeLabels[item.billing_mode] || item.billing_mode),
      labels: { style: { colors: chartText, fontSize: "10px" } },
      axisBorder: { show: false },
      axisTicks: { show: false },
    },
    yaxis: { labels: { style: { colors: chartText }, formatter: (value) => `${Math.round(value)} GB` } },
    legend: { labels: { colors: chartText }, fontSize: "11px" },
    tooltip: { theme: "dark" },
  }), [trafficModes]);

  if (getUserIsPending || query.isLoading) return <Skeleton height="520px" borderRadius="16px" mb={5} />;
  if (query.isError || !data) return <Alert status="error" mb={5} borderRadius="12px"><AlertIcon />آمار داشبورد بارگذاری نشد.<Button ms="auto" minH="40px" onClick={() => query.refetch()}>تلاش دوباره</Button></Alert>;

  const trend = data.new_users.change_percent === null
    ? "برای مقایسه، هفتهٔ قبل داده‌ای نیست"
    : `${data.new_users.change_percent >= 0 ? "+" : ""}${faNumber(data.new_users.change_percent)}٪ نسبت به هفتهٔ قبل`;
  const statusItems = [
    { label: "فعال", value: data.active_users, color: "#42b97b" },
    { label: "در انتظار", value: data.on_hold_users, color: "#d7ad54" },
    { label: "غیرفعال", value: data.disabled_users, color: "#8a9290" },
    { label: "منقضی", value: data.expired_users, color: "#ef6b65" },
    { label: "محدود", value: data.limited_users, color: "#b779d0" },
  ];
  const statusTotal = statusItems.reduce((sum, item) => sum + item.value, 0);
  const donutOptions: ApexOptions = {
    chart: { animations: { enabled: false }, background: "transparent", fontFamily: "Vazirmatn" },
    colors: statusItems.map((item) => item.color),
    labels: statusItems.map((item) => item.label),
    legend: { show: false },
    dataLabels: { enabled: false },
    stroke: { width: 2, colors: ["var(--panel-surface)"] },
    plotOptions: { pie: { donut: { size: "72%", labels: { show: true, name: { show: true, color: chartText }, value: { show: true, color: "#f5f5f4", formatter: (value) => faNumber(Number(value)) }, total: { show: true, label: "وضعیت", color: chartText, formatter: () => "" } } } } },
    tooltip: { theme: "dark" },
  };
  const accountData = account.data;
  const canCreatePlan = accountData?.role === "OWNER" || Boolean(accountData?.can_manage_plans);
  const memoryPercent = system.data?.mem_total ? Math.min(100, (system.data.mem_used / system.data.mem_total) * 100) : 0;
  const cpuPercent = Math.min(100, Math.max(0, system.data?.cpu_usage || 0));
  const liveBandwidth = (system.data?.incoming_bandwidth_speed || 0) + (system.data?.outgoing_bandwidth_speed || 0);

  return (
    <Stack spacing={3} mb={5} aria-live="polite">
      {accountData?.account_status !== "ACTIVE" && accountData && (
        <Alert status="warning" borderRadius="12px" borderWidth="1px"><AlertIcon />این حساب فقط قابل مشاهده است. دلیل: {accountData.suspended_reason || accountData.account_status}</Alert>
      )}

      <SimpleGrid columns={{ base: 2, xl: 5 }} gap={3}>
        <Card p={3.5} minH="118px" gridColumn={{ base: "span 2", xl: "span 2" }} bg="linear-gradient(145deg, var(--panel-surface), var(--panel-nested))" color="inherit" borderWidth="1px" borderColor="var(--panel-border-strong)" borderRadius="14px">
          {account.isLoading ? <Skeleton h="90px" borderRadius="10px" /> : account.isError || !accountData ? (
            <Alert status="error" borderRadius="10px"><AlertIcon />وضعیت حساب بارگذاری نشد.</Alert>
          ) : (
            <Stack spacing={2.5}>
              <HStack justify="space-between" align="start" gap={3}>
                <Box minW={0}>
                  <HStack spacing={1.5} flexWrap="wrap">
                    <Badge colorScheme={accountData.role === "OWNER" ? "purple" : "gray"}>{roleLabels[accountData.role]}</Badge>
                    <Badge colorScheme="yellow">{billingModeLabels[accountData.billing_mode]}</Badge>
                    <Badge colorScheme={accountData.user_creation_mode === "PLAN_ONLY" ? "blue" : "green"}>{accountData.user_creation_mode === "PLAN_ONLY" ? "ساخت فقط با پلن" : "ساخت سفارشی"}</Badge>
                  </HStack>
                  <Text mt={2} color="gray.400" fontSize="xs">{accountData.role === "OWNER" ? "دسترسی مالک" : "اعتبار قابل استفاده"}</Text>
                  <Text mt={0.5} fontSize="2xl" fontWeight="800">{accountData.role === "OWNER" ? "بدون سقف" : `${faNumber(accountData.money_balance_toman)} تومان`}</Text>
                </Box>
                <Box flexShrink={0} p={2} color="primary.300" bg="rgba(255,255,255,.04)" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px"><BoltIcon width={20} /></Box>
              </HStack>
              {accountData.role === "OWNER" ? (
                <Text color="gray.400" fontSize="xs">تمام محدودیت‌های تجاری حساب برای مالک غیرفعال است.</Text>
              ) : (
                <Text color="gray.400" fontSize="xs">{accountData.billing_mode === "USED_TRAFFIC" ? `قیمت خرید هر گیگ: ${faNumber(accountData.used_traffic_price_per_gib_toman || 0)} تومان` : "خرید و تمدید از قیمت پلن کسر می‌شود."}</Text>
              )}
            </Stack>
          )}
        </Card>
        <Kpi label="کل کاربران" value={faNumber(data.total_users)} detail="در محدوده‌ای که اجازه مدیریت آن را دارید" icon={<UsersKpiIcon />} />
        <Kpi label="آنلاین در ۲۴ ساعت" value={faNumber(data.online_users)} detail="کاربرانی که در ۲۴ ساعت اخیر اتصال داشته‌اند" icon={<OnlineIcon />} />
        <Kpi fullMobile label="کاربر جدید این هفته" value={faNumber(data.new_users.current)} detail={trend} icon={<NewUsersIcon />} />
      </SimpleGrid>

      <Button display={{ base: "inline-flex", md: "none" }} minH="44px" variant="outline" borderColor="var(--panel-border-strong)" aria-expanded={mobileDetailsOpen} onClick={() => setMobileDetailsOpen((value) => !value)}>
        {mobileDetailsOpen ? "بستن جزئیات داشبورد" : "نمایش نمودارها و فعالیت‌ها"}
      </Button>

      <Collapse in={desktopDetailsVisible || mobileDetailsOpen} animateOpacity={false}>
      <Stack spacing={3}>
      <SimpleGrid columns={{ base: 1, xl: 5 }} gap={3}>
        <Card p={3.5} minW={0} gridColumn={{ xl: "span 3" }} bg="var(--panel-surface)" color="inherit" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="14px">
          <Box><Text as="h2" fontWeight="800">ترکیب وضعیت کاربران</Text><Text mt={1} color="gray.400" fontSize="xs">هر کاربر دقیقاً در یکی از وضعیت‌های زیر شمرده می‌شود.</Text></Box>
          {statusTotal === 0 ? <Text py={12} textAlign="center" color="gray.500">هنوز کاربری ثبت نشده است.</Text> : (
            <SimpleGrid columns={{ base: 1, md: 2 }} alignItems="center" gap={2} mt={2}>
              <Box h="210px" minW={0} dir="ltr" aria-label="نمودار وضعیت کاربران"><Chart type="donut" height="100%" options={donutOptions} series={statusItems.map((item) => item.value)} /></Box>
              <Stack spacing={1.5}>
                {statusItems.map((item) => (
                  <HStack key={item.label} minH="34px" px={2.5} py={1.5} justify="space-between" bg="var(--panel-nested)" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="9px">
                    <HStack><Box boxSize="8px" borderRadius="full" bg={item.color} /><Text color="gray.300" fontSize="xs">{item.label}</Text></HStack>
                    <Text fontWeight="800" fontSize="sm">{faNumber(item.value)}</Text>
                  </HStack>
                ))}
              </Stack>
            </SimpleGrid>
          )}
        </Card>

        <Card p={3.5} minW={0} gridColumn={{ xl: "span 2" }} bg="var(--panel-surface)" color="inherit" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="14px">
          <HStack justify="space-between" align="start" gap={3}><Box><Text as="h2" fontWeight="800">آخرین فعالیت‌ها</Text><Text mt={1} color="gray.400" fontSize="xs">رخدادهای واقعی در محدوده مدیریتی شما</Text></Box><ClockIcon width={19} color="var(--panel-accent)" aria-hidden="true" /></HStack>
          {activity.isLoading ? <Stack mt={3}>{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} h="42px" borderRadius="9px" />)}</Stack> : activity.isError ? (
            <Text mt={4} color="red.300" fontSize="sm">فعالیت‌ها بارگذاری نشدند.</Text>
          ) : !activity.data?.length ? (
            <Text mt={5} color="gray.500" fontSize="sm">هنوز فعالیتی ثبت نشده است.</Text>
          ) : (
            <Stack mt={3} spacing={1.5}>
              {activity.data.map((item) => (
                <HStack key={item.id} minH="42px" px={2.5} py={1.5} justify="space-between" align="center" gap={3} bg="var(--panel-nested)" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="9px">
                  <HStack minW={0} spacing={2}><Box flexShrink={0} boxSize="7px" borderRadius="full" bg={item.status === "failed" ? "red.400" : "green.400"} /><Text minW={0} noOfLines={1} fontSize="xs">{activityText(item)}</Text></HStack>
                  <Text flexShrink={0} color="gray.500" fontSize="10px" dir="ltr">{activityTime(item.created_at)}</Text>
                </HStack>
              ))}
            </Stack>
          )}
          <Button mt={3} minH="44px" w="full" size="sm" variant="outline" borderColor="var(--panel-border-strong)" onClick={() => navigate("/audit-logs/")}>مشاهده گزارش کامل</Button>
        </Card>
      </SimpleGrid>

      {trafficModes.length > 1 && (
        <Card p={3.5} bg="var(--panel-surface)" color="inherit" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="14px" minW={0}>
          <HStack justify="space-between" align="start" gap={3} flexWrap="wrap"><Box><Text as="h2" fontWeight="800">ترافیک به تفکیک نوع اعتبار</Text><Text mt={1} color="gray.400" fontSize="xs">فقط مدل‌هایی نمایش داده می‌شوند که مصرف یا حجم تعریف‌شده دارند.</Text></Box><Badge colorScheme="yellow">GB</Badge></HStack>
          <Box h={{ base: "190px", md: "220px" }} minW={0} dir="ltr" aria-label="نمودار ترافیک بر اساس نوع اعتبار"><Chart type="bar" height="100%" options={barOptions} series={[
            { name: "مصرف ثبت‌شده", data: trafficModes.map((item) => Number(((item.current_used_traffic ?? 0) / 1073741824).toFixed(2))) },
            { name: "حجم تعریف‌شده", data: trafficModes.map((item) => Number((item.allocated_quota / 1073741824).toFixed(2))) },
          ]} /></Box>
        </Card>
      )}

      {isOwner && (
        <Card p={3.5} bg="var(--panel-surface)" color="inherit" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="14px">
          <HStack justify="space-between" align="start" gap={3} mb={3}><Box><Text as="h2" fontWeight="800">منابع سرور</Text><Text mt={1} color="gray.400" fontSize="xs">اعداد زندهٔ سیستم؛ آمار کاربران و مصرف در این بخش تکرار نشده است.</Text></Box><HStack>{backups.data?.[0] && <Badge colorScheme={backups.data[0].generation_status === "SUCCESS" ? "blue" : "red"}>Backup {backups.data[0].generation_status}</Badge>}{system.data?.version && <Badge colorScheme="green" dir="ltr">v{system.data.version}</Badge>}</HStack></HStack>
          {system.isLoading ? <Skeleton h="105px" borderRadius="12px" /> : system.isError || !system.data ? <Text color="red.300" fontSize="sm">منابع سرور بارگذاری نشدند.</Text> : (
            <SimpleGrid columns={{ base: 1, md: 3 }} gap={2.5}>
              <ResourceMeter label="پردازنده" value={`${faNumber(Math.round(cpuPercent))}٪`} detail="مصرف لحظه‌ای CPU" percent={cpuPercent} icon={<CpuIcon />} />
              <ResourceMeter label="حافظه" value={String(formatBytes(system.data.mem_used))} detail={`از ${formatBytes(system.data.mem_total)}`} percent={memoryPercent} icon={<ServerIcon />} />
              <ResourceMeter label="سرعت لحظه‌ای شبکه" value={`${formatBytes(liveBandwidth)}/s`} detail="مجموع ورودی و خروجی" icon={<TrafficIcon />} />
            </SimpleGrid>
          )}
        </Card>
      )}
      </Stack>
      </Collapse>

      <HStack color="gray.500" fontSize="10px" justify="end"><ClockIcon width={13} aria-hidden="true" /><Text>آخرین محاسبه: {new Date(data.generated_at).toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" })}</Text></HStack>

      {accountData?.account_status === "ACTIVE" && <Box position="fixed" left={{ base: 4, md: 6 }} right="auto" bottom={{ base: 4, md: 6 }} zIndex="popover">
        {quickOpen && <Stack mb={2} p={2} minW="190px" bg="var(--panel-nested)" borderWidth="1px" borderColor="var(--panel-border-strong)" borderRadius="14px" boxShadow="0 18px 48px rgba(0,0,0,.5)">
          {accountData?.user_creation_mode === "FREE_FORM"
            ? <QuickAction label="افزودن کاربر" icon={<UserPlusIcon width={25} />} onClick={() => { useDashboard.getState().onCreateUser(true); setQuickOpen(false); }} />
            : <QuickAction label="ساخت کاربر از پلن" icon={<UserPlusIcon width={25} />} onClick={() => navigate("/plans/")} />}
          {capabilities.data?.can_create_admins && <QuickAction label="ساخت ادمین" icon={<UserGroupIcon width={25} />} onClick={() => { onCreateAdmin(); setQuickOpen(false); }} />}
          {canCreatePlan && <QuickAction label="ساخت پلن" icon={<RectangleStackIcon width={25} />} onClick={() => { onCreatePlan(); setQuickOpen(false); }} />}
        </Stack>}
        <IconButton aria-label={quickOpen ? "بستن دسترسی سریع" : "بازکردن دسترسی سریع"} aria-expanded={quickOpen} icon={<BoltIcon width={24} />} onClick={() => setQuickOpen((value) => !value)} boxSize="54px" borderRadius="full" colorScheme="primary" color="#07130e" boxShadow="0 12px 30px rgba(0,0,0,.45)" />
      </Box>}
    </Stack>
  );
};
