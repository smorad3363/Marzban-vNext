import { Badge, Box, Button, Card, Collapse, HStack, Text, useBreakpointValue, useDisclosure } from "@chakra-ui/react";
import { AdminFormDrawer } from "components/AdminFormDrawer";
import { AppShell } from "components/AppShell";
import { DashboardOverview } from "components/DashboardOverview";
import { CoreSettingsModal } from "components/CoreSettingsModal";
import { DeleteUserModal } from "components/DeleteUserModal";
import { Filters } from "components/Filters";
import { HostsDialog } from "components/HostsDialog";
import { NodesDialog } from "components/NodesModal";
import { NodesUsage } from "components/NodesUsage";
import { PlanCreateModal } from "components/PlanCreateModal";
import { QRCodeDialog } from "components/QRCodeDialog";
import { ResetAllUsageModal } from "components/ResetAllUsageModal";
import { ResetUserUsageModal } from "components/ResetUserUsageModal";
import { RevokeSubscriptionModal } from "components/RevokeSubscriptionModal";
import { UserDialog } from "components/UserDialog";
import { UsersTable } from "components/UsersTable";
import { fetchInbounds, useDashboard } from "contexts/DashboardContext";
import { FC, useEffect, useState } from "react";
import useGetUser from "hooks/useGetUser";

const calendarParts = (date: Date, calendar: "persian" | "islamic") => {
  const parts = new Intl.DateTimeFormat(`en-US-u-ca-${calendar}`, { month: "numeric", day: "numeric" }).formatToParts(date);
  return {
    month: Number(parts.find((item) => item.type === "month")?.value || 0),
    day: Number(parts.find((item) => item.type === "day")?.value || 0),
  };
};

const iranHoliday = (date: Date) => {
  if (date.getDay() === 5) return "تعطیل هفتگی جمعه";
  const solar = calendarParts(date, "persian");
  const fixed: Record<string, string> = {
    "1/1": "نوروز", "1/2": "نوروز", "1/3": "نوروز", "1/4": "نوروز",
    "1/12": "روز جمهوری اسلامی", "1/13": "روز طبیعت", "3/14": "رحلت امام خمینی",
    "3/15": "قیام پانزده خرداد", "11/22": "پیروزی انقلاب اسلامی", "12/29": "ملی‌شدن صنعت نفت",
  };
  if (fixed[`${solar.month}/${solar.day}`]) return fixed[`${solar.month}/${solar.day}`];
  const lunar = calendarParts(date, "islamic");
  const religious: Record<string, string> = {
    "1/9": "تاسوعا", "1/10": "عاشورا", "2/20": "اربعین", "2/28": "رحلت پیامبر",
    "2/30": "شهادت امام رضا", "3/17": "میلاد پیامبر", "6/3": "شهادت حضرت فاطمه",
    "7/13": "میلاد امام علی", "7/27": "مبعث", "8/15": "نیمه شعبان",
    "9/21": "شهادت امام علی", "10/1": "عید فطر", "10/2": "تعطیل عید فطر",
    "12/10": "عید قربان", "12/18": "عید غدیر",
  };
  return religious[`${lunar.month}/${lunar.day}`] || null;
};

export const Dashboard: FC = () => {
  const { userData } = useGetUser();
  const isOwner = userData.role === "OWNER" || userData.is_sudo;
  const desktopUsersVisible = useBreakpointValue({ base: false, md: true }) ?? false;
  const adminCreate = useDisclosure();
  const planCreate = useDisclosure();
  const [mobileUsersOpen, setMobileUsersOpen] = useState(false);
  const today = new Date();
  const holiday = iranHoliday(today);
  useEffect(() => {
    useDashboard.getState().refetchUsers();
    fetchInbounds();
  }, []);
  useEffect(() => {
    const openUsers = () => setMobileUsersOpen(true);
    window.addEventListener("open-users-panel", openUsers);
    return () => window.removeEventListener("open-users-panel", openUsers);
  }, []);
  return (
    <AppShell>
      <Card as="header" p={{ base: 4, md: 5 }} mb={3} bg="linear-gradient(145deg, var(--panel-surface), var(--panel-nested))" color="inherit" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="14px">
      <HStack justify="space-between" align="center" flexWrap="wrap" gap={3}>
        <Box>
          <Text color="primary.600" _dark={{ color: "primary.300" }} fontSize="xs" fontWeight="800">مرکز کنترل</Text>
          <Text as="h1" mt={1} fontSize={{ base: "xl", md: "2xl" }} fontWeight="800" letterSpacing="-0.035em">
            خوش آمدی، <Text as="span" dir="ltr">{userData.username}</Text>
          </Text>
          <Text color="gray.600" _dark={{ color: "gray.400" }} mt={1} fontSize="sm">وضعیت حساب، کاربران، فعالیت‌ها و منابع مجاز را یکجا ببینید.</Text>
        </Box>
        <Box textAlign="end" px={3} py={2} bg="rgba(255,255,255,.03)" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px">
          <Text fontSize="sm" fontWeight="750">{today.toLocaleDateString("fa-IR-u-ca-persian", { weekday: "long", year: "numeric", month: "long", day: "numeric" })}</Text>
          <Badge mt={1} colorScheme={holiday ? "orange" : "green"}>{holiday || "روز کاری"}</Badge>
        </Box>
      </HStack>
      </Card>
      <DashboardOverview onCreateAdmin={adminCreate.onOpen} onCreatePlan={planCreate.onOpen} />
      <Box as="section" aria-labelledby="user-operations-title">
        <Text color="primary.600" _dark={{ color: "primary.300" }} fontSize="xs" fontWeight="800">کاربران</Text>
        <Text id="user-operations-title" as="h2" mt={1} fontSize="lg" fontWeight="800">مدیریت کاربران</Text>
        <HStack justify="space-between" align="center" mt={1} gap={3}>
          <Text color="gray.600" _dark={{ color: "gray.400" }} fontSize="sm">کاربر بسازید، جست‌وجو کنید یا فیلترها را تغییر دهید.</Text>
          <Button display={{ base: "inline-flex", md: "none" }} size="sm" variant="outline" borderColor="var(--panel-border)" aria-expanded={mobileUsersOpen} onClick={() => setMobileUsersOpen((value) => !value)}>{mobileUsersOpen ? "بستن کاربران" : "نمایش کاربران"}</Button>
        </HStack>
      <Collapse in={desktopUsersVisible || mobileUsersOpen} animateOpacity={false}>
      <Box
        mt={3}
        w="full"
        minW={0}
        bg="var(--panel-surface)"
        borderWidth="1px"
        borderColor="rgba(148, 163, 184, .14)"
        borderRadius="14px"
        boxShadow="0 18px 48px rgba(0, 0, 0, .24)"
        backdropFilter="blur(18px)"
        overflow="hidden"
      >
        <Filters />
        <Box px={{ base: 3, md: 4, xl: 5 }} pb={5} minW={0}>
          <UsersTable />
        </Box>
      </Box>
      </Collapse>
      </Box>
        <UserDialog />
        <AdminFormDrawer isOpen={adminCreate.isOpen} admin={null} onClose={adminCreate.onClose} />
        <PlanCreateModal isOpen={planCreate.isOpen} isOwner={isOwner} onClose={planCreate.onClose} />
        <DeleteUserModal />
        <QRCodeDialog />
        <ResetUserUsageModal />
        <RevokeSubscriptionModal />
        {isOwner && (
          <>
            <HostsDialog />
            <NodesDialog />
            <NodesUsage />
            <ResetAllUsageModal />
            <CoreSettingsModal />
          </>
        )}
    </AppShell>
  );
};

export default Dashboard;
