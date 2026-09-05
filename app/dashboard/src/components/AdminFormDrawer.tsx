import {
  Alert, AlertIcon, Badge, Box, Button, Checkbox, Code,
  Flex, FormControl, FormHelperText, FormLabel, HStack, Input,
  Modal, ModalBody, ModalCloseButton, ModalContent, ModalFooter, ModalHeader, ModalOverlay,
  SimpleGrid, Skeleton, Stack, Switch, Tag, TagCloseButton, TagLabel, Text,
  useToast,
} from "@chakra-ui/react";
import { useDashboard } from "contexts/DashboardContext";
import { ChangeEvent, FC, FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "react-query";
import { fetch } from "service/http";
import {
  AdminCapabilities, AdminPolicy, ManagedAdmin, ManagedAdminList, ManagedAdminPayload,
  SubscriptionMode,
} from "types/Admin";
import { localizedApiError } from "utils/apiError";
type BillingMode = AdminPolicy["billing_mode"];

const billingLabels: Record<BillingMode, { title: string; help: string }> = {
  USED_TRAFFIC: { title: "مصرف واقعی", help: "اعتبار با مصرف واقعی کاربران این شاخه کم می‌شود." },
  ALLOCATED_TRAFFIC: { title: "حجم ساخته‌شده", help: "اعتبار هنگام اختصاص حجم به کاربر کم می‌شود." },
  USER_CREDIT: { title: "حجم نامحدود · سقف اکانت", help: "حجم نامحدود است و محدودیت با تعداد اکانت محاسبه می‌شود." },
  LEGACY_COMPAT: { title: "حالت قدیمی", help: "فقط برای مشاهده ادمین‌های مهاجرت‌داده‌شده." },
  SEAT_CREDIT: { title: "اعتبار دستگاه قدیمی", help: "فقط برای سازگاری رکوردهای قبلی." },
};

const accessPolicyOptions = [
  { key: "prevent_user_deletion", label: "admins.preventDelete", help: "admins.preventDeleteHelp" },
  { key: "prevent_user_reset", label: "admins.preventReset", help: "admins.preventResetHelp" },
  { key: "prevent_unlimited_traffic", label: "admins.preventUnlimited", help: "admins.preventUnlimitedHelp" },
] as const;

const emptyPolicy = (): AdminPolicy => ({
  billing_mode: "USED_TRAFFIC", total_traffic: null, expiry_date: null,
  money_billing_enabled: true, money_balance_toman: 0, used_traffic_price_per_gib_toman: null,
  user_limit: null, max_users: null, device_capacity_limit: null,
  admin_traffic_warning_percent: 80, sudo_traffic_warning_percent: 80,
  all_inbounds: true, allowed_inbounds: [], all_user_limits: true,
  allowed_user_limits: [], allowed_subscription_modes: [
    "limited_traffic_unlimited_devices", "unlimited_traffic_limited_devices",
    "limited_traffic_limited_devices",
  ],
  view_full_client_ip: true, max_user_duration_days: null,
  calculate_volume: "used_traffic", prevent_user_creation: false,
  prevent_user_deletion: false, prevent_user_reset: false,
  prevent_revoke_subscription: false, prevent_unlimited_traffic: false,
});

const emptyAdmin = (): ManagedAdminPayload => ({
  username: "", password: "", is_sudo: false, role: "ADMIN",
  telegram_id: null, phone: "", discord_webhook: null,
  policy: emptyPolicy(), plan_category_ids: [], can_create_admins: false,
  plan_prices: [], initial_money_credit_toman: 0,
  user_creation_mode: "PLAN_ONLY", can_manage_plans: false,
  can_delegate_admin_creation: false, can_create_allocated_children: true,
  admin_creation_limit: 0,
});

const Section: FC<{ title: string; description?: string; children: ReactNode }> = ({ title, description, children }) => (
  <Box p={{ base: 3, md: 4 }} bg="var(--panel-nested)" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="12px">
    <Text as="h3" fontWeight="800" fontSize="sm">{title}</Text>
    {description && <Text color="gray.400" fontSize="xs" mt={1}>{description}</Text>}
    <Box mt={4}>{children}</Box>
  </Box>
);

type Props = { isOpen: boolean; admin: ManagedAdmin | null; onClose: () => void };

export const AdminFormDrawer: FC<Props> = ({ isOpen, admin, onClose }) => {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { inbounds } = useDashboard();
  const usernameRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState<ManagedAdminPayload>(emptyAdmin());
  const [billingMode, setBillingMode] = useState<BillingMode | "">("");
  const [inboundSearch, setInboundSearch] = useState("");
  const [newUserLimit, setNewUserLimit] = useState("");
  const [creditAmount, setCreditAmount] = useState("");
  const [creditReason, setCreditReason] = useState("");
  const [creditBalance, setCreditBalance] = useState<number | null>(null);
  const isEditing = Boolean(admin);

  const capabilitiesQuery = useQuery<AdminCapabilities, Error>(
    "admin-capabilities", () => fetch("/admin/capabilities"),
    { enabled: isOpen, staleTime: 15000 }
  );
  useEffect(() => {
    if (admin) {
      setForm({
        username: admin.username, password: "", is_sudo: admin.is_sudo,
        role: "ADMIN", telegram_id: admin.telegram_id, phone: admin.phone,
        discord_webhook: admin.discord_webhook, policy: {
          ...admin.policy,
          view_full_client_ip: true,
          prevent_user_creation: false,
          prevent_revoke_subscription: false,
        },
        plan_category_ids: [], plan_prices: undefined, initial_money_credit_toman: 0,
        user_creation_mode: admin.user_creation_mode,
        can_manage_plans: admin.can_manage_plans,
        can_create_admins: admin.can_create_admins,
        can_delegate_admin_creation: admin.can_delegate_admin_creation,
        can_create_allocated_children: admin.can_create_allocated_children,
        admin_creation_limit: admin.admin_creation_limit,
      });
      setBillingMode(admin.policy.billing_mode);
    } else {
      setForm(emptyAdmin());
      setBillingMode("");
    }
    setInboundSearch(""); setNewUserLimit(""); setCreditAmount(""); setCreditReason("");
    setCreditBalance(admin?.policy.money_balance_toman ?? null);
  }, [admin, isOpen]);

  const availableInbounds = useMemo(() => [...inbounds.values()].flat().filter((item) =>
    item.tag.toLocaleLowerCase().includes(inboundSearch.trim().toLocaleLowerCase())
  ), [inbounds, inboundSearch]);

  const mutation = useMutation<ManagedAdmin, Error, ManagedAdminPayload>((payload) => fetch(
    isEditing ? `/admin-management/${admin?.username}` : "/admin-management",
    { method: isEditing ? "PUT" : "POST", body: payload }
  ), {
    onSuccess: (savedAdmin) => {
      if (isEditing) {
        queryClient.setQueriesData<ManagedAdminList | undefined>("admin-management", (current) => current ? ({
          ...current,
          admins: current.admins.map((item) => item.id === savedAdmin.id ? savedAdmin : item),
        }) : current);
      }
      queryClient.invalidateQueries("admin-management");
      queryClient.invalidateQueries("admin-hierarchy-tree");
      queryClient.invalidateQueries("admin-capabilities");
      toast({ title: t(isEditing ? "admins.updated" : "admins.created"), status: "success", duration: 3000 });
      onClose();
    },
    onError: (error) => { toast({ title: t("admins.saveFailed"), description: localizedApiError(error), status: "error", duration: 5000 }); },
  });

  const creditMutation = useMutation<unknown, Error, { operation: "grant" | "reclaim"; amount: number }>(
    ({ operation, amount }) => fetch(`/admin-management/${encodeURIComponent(admin?.username || "")}/money/${operation}`, {
      method: "POST",
      body: { amount_toman: amount, idempotency_key: `admin-money-${crypto.randomUUID()}`, note: creditReason.trim() || undefined },
    }), {
      onSuccess: () => {
        setCreditAmount(""); setCreditReason("");
        queryClient.invalidateQueries("admin-management");
        queryClient.invalidateQueries("admin-hierarchy-tree");
        toast({ title: "اعتبار به‌روزرسانی شد", status: "success", duration: 3000 });
      },
      onError: (error) => { toast({ title: "تغییر اعتبار انجام نشد", description: localizedApiError(error), status: "error", duration: 5000 }); },
    }
  );

  const setField = <K extends keyof ManagedAdminPayload>(key: K, value: ManagedAdminPayload[K]) => setForm((current) => ({ ...current, [key]: value }));
  const setPolicy = <K extends keyof AdminPolicy>(key: K, value: AdminPolicy[K]) => setForm((current) => ({ ...current, policy: { ...current.policy, [key]: value } }));
  const nullableNumber = (event: ChangeEvent<HTMLInputElement>) => event.target.value === "" ? null : Number(event.target.value);

  const selectBillingMode = (mode: BillingMode | "") => {
    setBillingMode(mode);
    if (!mode) return;
    setForm((current) => ({
      ...current,
      user_creation_mode: mode === "USED_TRAFFIC" ? "FREE_FORM" : "PLAN_ONLY",
      can_manage_plans: mode === "USED_TRAFFIC" ? false : current.can_manage_plans,
      can_create_allocated_children: mode === "USED_TRAFFIC" && current.can_create_allocated_children,
      policy: {
        ...current.policy, billing_mode: mode,
        calculate_volume: mode === "ALLOCATED_TRAFFIC" ? "created_traffic" : "used_traffic",
        total_traffic: null, max_users: null, device_capacity_limit: null,
      },
    }));
  };

  const showWarning = (title: string) => { toast({ title, status: "warning", duration: 3000 }); return false; };
  const validate = () => {
    if (!form.username.trim()) return showWarning("نام کاربری را وارد کنید");
    if (!isEditing && !form.password) return showWarning(t("admins.passwordRequired"));
    if (form.phone && !/^09\d{9}$/.test(form.phone)) return showWarning("شماره تلفن باید با فرمت 09xxxxxxxxx باشد");
    if (!isEditing && !billingMode) return showWarning("نوع حساب فرزند را انتخاب کنید");
    if (mode === "USED_TRAFFIC" && form.policy.used_traffic_price_per_gib_toman === null) return showWarning("قیمت خرید هر گیگ را وارد کنید");
    if (!isEditing && form.initial_money_credit_toman < 0) return showWarning("اعتبار اولیه نامعتبر است");
    if (!form.policy.all_inbounds && !form.policy.allowed_inbounds.length) return showWarning(t("admins.selectInboundRequired"));
    if (!form.policy.all_user_limits && !form.policy.allowed_user_limits.length) return showWarning(t("admins.selectUserLimitRequired"));
    if (!form.policy.allowed_subscription_modes.length) return showWarning(t("admins.selectSubscriptionModeRequired"));
    return true;
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!validate()) return;
    const payload = {
      ...form,
      role: "ADMIN" as const,
      user_creation_mode: mode === "USED_TRAFFIC" ? "FREE_FORM" as const : "PLAN_ONLY" as const,
      can_manage_plans: mode === "USED_TRAFFIC" ? false : form.can_manage_plans,
      phone: form.phone?.trim() || null,
      policy: {
        ...form.policy,
        view_full_client_ip: true,
        prevent_user_creation: false,
        prevent_revoke_subscription: false,
        money_billing_enabled: true,
      },
    };
    if (isEditing && !payload.password) delete payload.password;
    mutation.mutate(payload);
  };

  const toggleInbound = (tag: string, checked: boolean) => setPolicy("allowed_inbounds", (checked
    ? [...new Set([...form.policy.allowed_inbounds, tag])]
    : form.policy.allowed_inbounds.filter((value) => value !== tag)).sort());
  const addUserLimit = () => {
    const value = Number(newUserLimit);
    if (!Number.isInteger(value) || value < 1) return;
    setPolicy("allowed_user_limits", [...new Set([...form.policy.allowed_user_limits, value])].sort((a, b) => a - b));
    setNewUserLimit("");
  };
  const toggleSubscriptionMode = (mode: SubscriptionMode, checked: boolean) => setPolicy("allowed_subscription_modes", checked
    ? [...new Set([...form.policy.allowed_subscription_modes, mode])]
    : form.policy.allowed_subscription_modes.filter((value) => value !== mode));

  const mode = billingMode || form.policy.billing_mode;
  const parsedCreditAmount = Number(creditAmount);
  const creditAmountValid = Number.isInteger(parsedCreditAmount) && parsedCreditAmount > 0;
  const adjustCredit = (operation: "grant" | "reclaim") => {
    if (!creditAmountValid || !admin) return;
    if (operation === "reclaim" && !window.confirm(`اعتبار ${admin.username} کم شود؟`)) return;
    creditMutation.mutate({ operation, amount: parsedCreditAmount });
  };
  const displayedBalance = `${(creditBalance || 0).toLocaleString("fa-IR")} تومان`;
  const allowedModes = capabilitiesQuery.data?.allowed_child_billing_modes || [];
  const hierarchyReady = capabilitiesQuery.data?.hierarchy_enabled !== false;
  const subscriptionModes: SubscriptionMode[] = [
    "limited_traffic_unlimited_devices", "unlimited_traffic_limited_devices",
    "limited_traffic_limited_devices", "unlimited_traffic_unlimited_devices",
  ];

  return (
    <Modal isOpen={isOpen} onClose={onClose} size="5xl" scrollBehavior="inside" initialFocusRef={usernameRef} isCentered>
      <ModalOverlay bg="rgba(0,0,0,.72)" backdropFilter="blur(4px)" />
      <ModalContent as="form" onSubmit={submit} dir={i18n.dir()} mx={3} my={3} maxH="calc(100dvh - 24px)" overflow="hidden" bg="var(--panel-surface)" color="gray.100" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="12px" boxShadow="elevated">
        <ModalHeader px={{ base: 4, md: 5 }} py={4} borderBottomWidth="1px" borderColor="var(--panel-border)">
          <ModalCloseButton top={4} insetInlineStart={4} insetInlineEnd="auto" />
          <Box pe={12}><Text fontSize="lg" fontWeight="800">{t(isEditing ? "admins.editTitle" : "admins.createTitle")}</Text><Text mt={1} color="gray.400" fontSize="xs">فرم فشرده؛ همه تنظیمات اصلی و محدودیت‌های دسترسی یکجا.</Text></Box>
        </ModalHeader>

        <ModalBody px={{ base: 4, md: 5 }} py={4} overflowY="auto">
          {capabilitiesQuery.isLoading ? <Skeleton h="240px" borderRadius="12px" /> : capabilitiesQuery.isError ? <Alert status="error"><AlertIcon />مجوزهای حساب بارگذاری نشد.</Alert> : (
            <Stack spacing={3}>
              {!hierarchyReady && (
                <Alert status="warning" alignItems="flex-start" borderRadius="12px">
                  <AlertIcon mt={0.5} />
                  <Box>
                    <Text fontWeight="800">ساختار ادمین‌ها هنوز روی سرور فعال نشده است.</Text>
                    <Text mt={1} fontSize="sm">ابتدا روی سرور این دستور را اجرا کنید؛ سپس این فرم را دوباره باز کنید:</Text>
                    <Text mt={2}>Use command-line administration to promote an Owner.</Text>
                  </Box>
                </Alert>
              )}
              <Section title="تنظیمات مدیر" description="مشخصات، نوع حساب و دسترسی‌های اصلی را یکجا تنظیم کنید.">
                <SimpleGrid columns={{ base: 1, md: 3 }} gap={3}>
                  <FormControl isRequired><FormLabel>{t("admins.username")}</FormLabel><Input ref={usernameRef} value={form.username} isDisabled={isEditing} maxLength={34} dir="ltr" autoComplete="username" onChange={(e) => setField("username", e.target.value)} /></FormControl>
                  <FormControl isRequired={!isEditing}><FormLabel>{t("admins.password")}</FormLabel><Input type="password" value={form.password || ""} dir="ltr" autoComplete="new-password" placeholder={isEditing ? t("admins.passwordKeep") : ""} onChange={(e) => setField("password", e.target.value)} /></FormControl>
                  <FormControl><FormLabel>شماره تلفن</FormLabel><Input type="tel" inputMode="numeric" autoComplete="tel" maxLength={11} placeholder="09xxxxxxxxx" value={form.phone || ""} onChange={(e) => setField("phone", e.target.value)} dir="ltr" /></FormControl>
                </SimpleGrid>

                <Box mt={4} pt={4} borderTopWidth="1px" borderColor="var(--panel-border)">
                  <Text fontSize="sm" fontWeight="800">نوع حساب</Text>
                  {isEditing ? <Box><HStack mt={2}><Badge colorScheme="primary">{billingLabels[mode].title}</Badge><Text color="gray.400" fontSize="xs">{billingLabels[mode].help}</Text></HStack>{mode === "USED_TRAFFIC" && <FormControl mt={3} isRequired maxW="360px"><FormLabel>قیمت خرید هر گیگ (تومان)</FormLabel><Input type="number" min={0} step={1000} dir="ltr" value={form.policy.used_traffic_price_per_gib_toman ?? ""} onChange={(e) => setPolicy("used_traffic_price_per_gib_toman", nullableNumber(e))} /></FormControl>}</Box> : (
                    <SimpleGrid mt={2} columns={{ base: 1, md: Math.min(Math.max(allowedModes.length, 1), 3) }} gap={2}>
                      {allowedModes.filter((item) => item !== "LEGACY_COMPAT" && item !== "SEAT_CREDIT").map((item) => (
                        <Button key={item} type="button" minH="68px" h="auto" py={2.5} px={3} whiteSpace="normal" textAlign="start" justifyContent="flex-start" variant={billingMode === item ? "solid" : "outline"} colorScheme={billingMode === item ? "green" : "gray"} onClick={() => selectBillingMode(item)}>
                          <Box><Text fontWeight="800">{billingLabels[item].title}</Text><Text mt={1} fontSize="xs" fontWeight="400" opacity={0.78}>{billingLabels[item].help}</Text></Box>
                        </Button>
                      ))}
                    </SimpleGrid>
                  )}
                  {!isEditing && billingMode && <SimpleGrid mt={3} columns={{ base: 1, md: 2 }} gap={3}>
                    <FormControl><FormLabel>اعتبار اولیه (تومان)</FormLabel><Input type="number" min={0} step={1000} dir="ltr" value={form.initial_money_credit_toman} onChange={(e) => setField("initial_money_credit_toman", Math.max(0, Number(e.target.value || 0)))} /><FormHelperText>از کیف پول والد به این مدیر منتقل می‌شود.</FormHelperText></FormControl>
                    {billingMode === "USED_TRAFFIC" && <FormControl isRequired><FormLabel>قیمت خرید هر گیگ (تومان)</FormLabel><Input type="number" min={0} step={1000} dir="ltr" value={form.policy.used_traffic_price_per_gib_toman ?? ""} onChange={(e) => setPolicy("used_traffic_price_per_gib_toman", nullableNumber(e))} /><FormHelperText>قیمت فروش به زیرمدیر نباید از این کمتر باشد.</FormHelperText></FormControl>}
                  </SimpleGrid>}
                </Box>

                <SimpleGrid mt={4} pt={4} borderTopWidth="1px" borderColor="var(--panel-border)" columns={{ base: 1, lg: 2 }} gap={3}>
                  {mode !== "USED_TRAFFIC" && <HStack justify="space-between" p={3} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px"><Box><Text fontSize="sm" fontWeight="700">اجازه مدیریت پلن</Text><Text color="gray.400" fontSize="xs">ساخت و ویرایش پلن با مجوز والد.</Text></Box><Switch isChecked={form.can_manage_plans} isDisabled={!capabilitiesQuery.data?.can_delegate_plan_management} onChange={(e) => setField("can_manage_plans", e.target.checked)} /></HStack>}
                  <Box>
                    <HStack justify="space-between" minH="44px" p={3} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px"><Box><Text fontSize="sm" fontWeight="800">اجازه ساخت زیرمدیر</Text><Text color="gray.400" fontSize="xs">سهم باقی‌مانده: {capabilitiesQuery.data?.admin_creation_remaining ?? "نامحدود"}</Text></Box><Switch colorScheme="primary" isChecked={form.can_create_admins} isDisabled={!capabilitiesQuery.data?.can_delegate_admin_creation} onChange={(e) => setForm((current) => ({ ...current, can_create_admins: e.target.checked, can_delegate_admin_creation: e.target.checked ? current.can_delegate_admin_creation : false, admin_creation_limit: e.target.checked ? current.admin_creation_limit : 0 }))} /></HStack>
                    {form.can_create_admins && <Stack mt={2} spacing={2}><FormControl><FormLabel>تعداد مدیر قابل ساخت</FormLabel><Input type="number" min={0} dir="ltr" value={form.admin_creation_limit ?? ""} onChange={(e) => setField("admin_creation_limit", nullableNumber(e))} /></FormControl><HStack justify="space-between" p={3} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px"><Text fontSize="sm">اجازه واگذاری ساخت زیرمدیر</Text><Switch isChecked={form.can_delegate_admin_creation} isDisabled={!capabilitiesQuery.data?.can_delegate_admin_creation} onChange={(e) => setField("can_delegate_admin_creation", e.target.checked)} /></HStack>{mode === "USED_TRAFFIC" && capabilitiesQuery.data?.can_create_allocated_children && <HStack justify="space-between" p={3} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px"><Text fontSize="sm">اجازه ساخت فرزند «حجم ساخته‌شده»</Text><Switch isChecked={form.can_create_allocated_children} onChange={(e) => setField("can_create_allocated_children", e.target.checked)} /></HStack>}</Stack>}
                  </Box>
                </SimpleGrid>
              </Section>

              {isEditing && admin?.parent_admin_id !== null && mode !== "LEGACY_COMPAT" && (
                <Section title="تغییر سریع اعتبار" description={`اعتبار فعلی: ${displayedBalance}`}>
                  <Stack direction={{ base: "column", md: "row" }} align={{ md: "end" }} spacing={2}>
                    <FormControl maxW={{ md: "220px" }}><FormLabel>مقدار (تومان)</FormLabel><Input type="number" min={1} step={1000} dir="ltr" value={creditAmount} onChange={(e) => setCreditAmount(e.target.value)} /></FormControl>
                    <FormControl flex="1"><FormLabel>یادداشت اختیاری</FormLabel><Input maxLength={512} value={creditReason} onChange={(e) => setCreditReason(e.target.value)} /></FormControl>
                    <Button type="button" colorScheme="primary" isDisabled={!creditAmountValid} isLoading={creditMutation.isLoading} onClick={() => adjustCredit("grant")}>افزایش</Button>
                    <Button type="button" variant="outline" colorScheme="orange" isDisabled={!creditAmountValid} isLoading={creditMutation.isLoading} onClick={() => adjustCredit("reclaim")}>کاهش</Button>
                  </Stack>
                </Section>
              )}

              <Section title="محدودیت‌های اختیاری" description="خالی‌گذاشتن هر مورد یعنی بدون محدودیت.">
                <SimpleGrid columns={{ base: 1, md: 2, xl: 4 }} gap={3}>
                  {mode !== "USER_CREDIT" && <FormControl><FormLabel>بیشترین تعداد کاربر</FormLabel><Input type="number" min={1} dir="ltr" value={form.policy.max_users ?? ""} onChange={(e) => setPolicy("max_users", nullableNumber(e))} /></FormControl>}
                  <FormControl><FormLabel>بیشترین مدت اشتراک (روز)</FormLabel><Input type="number" min={1} dir="ltr" value={form.policy.max_user_duration_days ?? ""} onChange={(e) => setPolicy("max_user_duration_days", nullableNumber(e))} /></FormControl>
                  <FormControl><FormLabel>تاریخ پایان مدیر</FormLabel><Input type="date" dir="ltr" value={form.policy.expiry_date || ""} onChange={(e) => setPolicy("expiry_date", e.target.value || null)} /></FormControl>
                  <FormControl><FormLabel>{t("admins.telegramId")}</FormLabel><Input type="number" value={form.telegram_id ?? ""} dir="ltr" onChange={(e) => setField("telegram_id", nullableNumber(e))} /></FormControl>
                </SimpleGrid>
              </Section>

              <Box p={{ base: 3, md: 4 }} bg="var(--panel-nested)" borderWidth="1px" borderColor="var(--panel-border)" borderRadius="12px">
                <Text as="h3" fontWeight="800" fontSize="sm">پلن‌ها و محدودیت دسترسی</Text>
                <Text color="gray.400" fontSize="xs" mt={1}>این بخش همیشه باز است؛ ورودی، دستگاه و عملیات مجاز را تعیین می‌کند. قیمت هر پلن فقط داخل همان پلن تنظیم می‌شود.</Text>
                <Stack mt={4} spacing={3}>
                  <SimpleGrid columns={{ base: 1, lg: 2 }} gap={3}>
                    <Section title="ورودی‌های مجاز"><Checkbox isChecked={form.policy.all_inbounds} onChange={(e) => setPolicy("all_inbounds", e.target.checked)}>همه ورودی‌ها</Checkbox>{!form.policy.all_inbounds && <Stack mt={3}><Input value={inboundSearch} onChange={(e) => setInboundSearch(e.target.value)} placeholder="جست‌وجوی ورودی" /><Stack maxH="180px" overflowY="auto">{availableInbounds.map((item) => <Checkbox key={item.tag} minH="40px" isChecked={form.policy.allowed_inbounds.includes(item.tag)} onChange={(e) => toggleInbound(item.tag, e.target.checked)}><Text dir="ltr">{item.tag}</Text></Checkbox>)}</Stack></Stack>}</Section>
                    <Section title="تعداد دستگاه قابل انتخاب"><Checkbox isChecked={form.policy.all_user_limits} onChange={(e) => setPolicy("all_user_limits", e.target.checked)}>بدون محدودیت انتخاب</Checkbox>{!form.policy.all_user_limits && <Stack mt={3}><HStack><Input type="number" min={1} dir="ltr" value={newUserLimit} onChange={(e) => setNewUserLimit(e.target.value)} /><Button type="button" onClick={addUserLimit}>افزودن</Button></HStack><Flex gap={2} wrap="wrap">{form.policy.allowed_user_limits.map((limit) => <Tag key={limit}><TagLabel>{limit}</TagLabel><TagCloseButton onClick={() => setPolicy("allowed_user_limits", form.policy.allowed_user_limits.filter((value) => value !== limit))} /></Tag>)}</Flex></Stack>}</Section>
                  </SimpleGrid>
                  <Section title="نوع اشتراک‌های مجاز"><SimpleGrid columns={{ base: 1, md: 2 }} gap={2}>{subscriptionModes.map((item) => <Checkbox key={item} minH="42px" isChecked={form.policy.allowed_subscription_modes.includes(item)} onChange={(e) => toggleSubscriptionMode(item, e.target.checked)}>{t(`admins.subscriptionMode.${item}`)}</Checkbox>)}</SimpleGrid></Section>
                  <SimpleGrid columns={{ base: 1, md: 3 }} gap={2}>{accessPolicyOptions.map((item) => <HStack key={item.key} justify="space-between" align="start" p={3} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px"><Box pe={2}><Text fontSize="sm">{t(item.label)}</Text><Text mt={1} fontSize="xs" color="gray.400">{t(item.help)}</Text></Box><Switch flexShrink={0} isChecked={Boolean(form.policy[item.key])} onChange={(e) => setPolicy(item.key, e.target.checked as never)} /></HStack>)}</SimpleGrid>
                </Stack>
              </Box>
            </Stack>
          )}
        </ModalBody>

        <ModalFooter gap={2} px={{ base: 4, md: 5 }} py={3} borderTopWidth="1px" borderColor="var(--panel-border)" bg="var(--panel-surface)">
          <Button type="button" variant="ghost" onClick={onClose}>{t("cancel")}</Button><Box flex={1} /><Button type="submit" colorScheme="primary" isLoading={mutation.isLoading} isDisabled={capabilitiesQuery.isLoading || capabilitiesQuery.isError || !hierarchyReady}>{t("save")}</Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
};
