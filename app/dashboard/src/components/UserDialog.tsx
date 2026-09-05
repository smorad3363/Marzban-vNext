import {
  Alert,
  AlertIcon,
  Badge,
  Box,
  Button,
  Collapse,
  Divider,
  Flex,
  FormControl,
  FormErrorMessage,
  FormHelperText,
  FormLabel,
  Grid,
  GridItem,
  HStack,
  IconButton,
  Modal,
  ModalBody,
  ModalCloseButton,
  ModalContent,
  ModalFooter,
  ModalHeader,
  ModalOverlay,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  Tooltip,
  VStack,
  chakra,
  useColorMode,
  useToast,
} from "@chakra-ui/react";
import {
  ChartPieIcon,
  PencilIcon,
  UserPlusIcon,
} from "@heroicons/react/24/outline";
import { zodResolver } from "@hookform/resolvers/zod";
import { resetStrategy } from "constants/UserSettings";
import { FilterUsageType, useDashboard } from "contexts/DashboardContext";
import dayjs from "dayjs";
import useGetUser from "hooks/useGetUser";
import { localizedApiError } from "utils/apiError";
import { FC, useEffect, useState } from "react";
import ReactApexChart from "react-apexcharts";
import ReactDatePicker from "react-datepicker";
import { Controller, FormProvider, useForm, useWatch } from "react-hook-form";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "react-query";
import { fetch } from "service/http";
import { AccountSummary, AdminCapabilities, ManagedAdmin, ManagedAdminList, SubscriptionMode } from "types/Admin";
import {
  ProxyKeys,
  ProxyType,
  User,
  UserCreate,
  UserInbounds,
} from "types/User";
import { relativeExpiryDate } from "utils/dateFormatter";
import { z } from "zod";
import { DeleteIcon } from "./DeleteUserModal";
import { Icon } from "./Icon";
import { Input } from "./Input";
import { RadioGroup } from "./RadioGroup";
import { UsageFilter, createUsageConfig } from "./UsageFilter";
import { ReloadIcon } from "./Filters";
import classNames from "classnames";

const AddUserIcon = chakra(UserPlusIcon, {
  baseStyle: {
    w: 5,
    h: 5,
  },
});

const EditUserIcon = chakra(PencilIcon, {
  baseStyle: {
    w: 5,
    h: 5,
  },
});

const UserUsageIcon = chakra(ChartPieIcon, {
  baseStyle: {
    w: 5,
    h: 5,
  },
});

const SectionHeader: FC<{ title: string; description: string }> = ({
  title,
  description,
}) => (
  <Box minW={0}>
    <Text
      color="gold.300"
      fontSize="xs"
      fontWeight="800"
      letterSpacing="0.06em"
      lineHeight="1.8"
    >
      {title}
    </Text>
    <Text mt={1} color="gray.400" fontSize="xs" lineHeight="1.8" maxW="72ch">
      {description}
    </Text>
  </Box>
);

export type UserDialogProps = {};

export type FormType = Pick<UserCreate, keyof UserCreate> & {
  selected_proxies: ProxyKeys;
  concurrent_user_limit: number | null;
  owner_admin: string;
};

const formatUser = (user: User): FormType => {
  return {
    ...user,
    data_limit: user.data_limit
      ? Number((user.data_limit / 1073741824).toFixed(5))
      : user.data_limit,
    on_hold_expire_duration: user.on_hold_expire_duration
      ? Number(user.on_hold_expire_duration / (24 * 60 * 60))
      : user.on_hold_expire_duration,
    selected_proxies: Object.keys(user.proxies) as ProxyKeys,
    concurrent_user_limit: user.concurrent_user_limit ?? null,
    owner_admin: user.admin?.username || "",
  };
};
const getDefaultValues = (): FormType => {
  const defaultInbounds = Object.fromEntries(useDashboard.getState().inbounds);
  const inbounds: UserInbounds = {};
  for (const key in defaultInbounds) {
    inbounds[key] = defaultInbounds[key].map((i) => i.tag);
  }
  return {
    selected_proxies: Object.keys(defaultInbounds) as ProxyKeys,
    data_limit: null,
    expire: null,
    username: "",
    data_limit_reset_strategy: "no_reset",
    status: "active",
    on_hold_expire_duration: null,
    note: "",
    concurrent_user_limit: null,
    owner_admin: "",
    inbounds,
    proxies: {
      vless: { id: "", flow: "" },
      vmess: { id: "" },
      trojan: { password: "" },
      shadowsocks: { password: "", method: "chacha20-ietf-poly1305" },
    },
  };
};

const mergeProxies = (
  proxyKeys: ProxyKeys,
  proxyType: ProxyType | undefined
): ProxyType => {
  const proxies: ProxyType = proxyKeys.reduce(
    (ac, a) => ({ ...ac, [a]: {} }),
    {}
  );
  if (!proxyType) return proxies;
  proxyKeys.forEach((proxy) => {
    if (proxyType[proxy]) {
      proxies[proxy] = proxyType[proxy];
    }
  });
  return proxies;
};

const baseSchema = {
  username: z.string().min(1, { message: "Required" }),
  selected_proxies: z.array(z.string()).refine((value) => value.length > 0, {
    message: "userDialog.selectOneProtocol",
  }),
  note: z.string().nullable(),
  proxies: z
    .record(z.string(), z.record(z.string(), z.any()))
    .transform((ins) => {
      const deleteIfEmpty = (obj: any, key: string) => {
        if (obj && obj[key] === "") {
          delete obj[key];
        }
      };
      deleteIfEmpty(ins.vmess, "id");
      deleteIfEmpty(ins.vless, "id");
      deleteIfEmpty(ins.trojan, "password");
      deleteIfEmpty(ins.shadowsocks, "password");
      deleteIfEmpty(ins.shadowsocks, "method");
      return ins;
    }),
  data_limit: z
    .string()
    .min(0)
    .or(z.number())
    .nullable()
    .transform((str) => {
      if (str) return Number((parseFloat(String(str)) * 1073741824).toFixed(5));
      return 0;
    }),
  expire: z.number().nullable(),
  data_limit_reset_strategy: z.string(),
  concurrent_user_limit: z.preprocess(
    (value) => value === "" || value === null ? null : Number(value),
    z.number().int().min(1).nullable()
  ),
  owner_admin: z.string(),
  inbounds: z.record(z.string(), z.array(z.string())).transform((ins) => {
    Object.keys(ins).forEach((protocol) => {
      if (Array.isArray(ins[protocol]) && !ins[protocol]?.length)
        delete ins[protocol];
    });
    return ins;
  }),
};

const schema = z.discriminatedUnion("status", [
  z.object({
    status: z.literal("active"),
    ...baseSchema,
  }),
  z.object({
    status: z.literal("disabled"),
    ...baseSchema,
  }),
  z.object({
    status: z.literal("limited"),
    ...baseSchema,
  }),
  z.object({
    status: z.literal("expired"),
    ...baseSchema,
  }),
  z.object({
    status: z.literal("on_hold"),
    on_hold_expire_duration: z.coerce
      .number()
      .min(0.1, "Required")
      .transform((d) => {
        return d * (24 * 60 * 60);
      }),
    ...baseSchema,
  }),
]);

const fetchAssignableAdmins = async (): Promise<ManagedAdmin[]> => {
  const admins: ManagedAdmin[] = [];
  let offset = 0;
  let total = 0;

  do {
    const page = await fetch<ManagedAdminList>(
      `/admin-management?offset=${offset}&limit=100`
    );
    admins.push(...page.admins);
    total = page.total;
    if (page.admins.length === 0) break;
    offset += page.admins.length;
  } while (offset < total);

  return admins;
};

const unrestrictedCapabilities: AdminCapabilities = {
  hierarchy_enabled: false,
  all_inbounds: true,
  allowed_inbounds: [],
  all_user_limits: true,
  allowed_user_limits: [],
  allowed_subscription_modes: [
    "limited_traffic_unlimited_devices",
    "unlimited_traffic_limited_devices",
    "limited_traffic_limited_devices",
    "unlimited_traffic_unlimited_devices",
  ],
  view_full_client_ip: true,
  capacity_used: 0,
  capacity_limit: null,
  capacity_remaining: null,
  quota: {
    current_users: 0,
    lifetime_consumed_traffic: 0,
    lifetime_created_traffic: 0,
    max_users: null,
    remaining_user_slots: null,
    credit_limit: null,
    credit_used: 0,
    credit_remaining: null,
    credit_usage_percent: null,
    credit_calculation_mode: "used_traffic",
    operation_allowance_remaining: null,
    admin_warning_percent: 80,
    sudo_warning_percent: 80,
    admin_warning_active: false,
    sudo_warning_active: false,
  },
  can_manage_admins: false,
  can_create_admins: false,
  can_delegate_admin_creation: false,
  can_create_allocated_children: false,
  admin_creation_limit: 0,
  admin_creations_used: 0,
  delegated_admin_creation_limit: 0,
  admin_creation_remaining: 0,
  allowed_child_roles: [],
  allowed_child_billing_modes: [],
  allowed_child_user_creation_modes: [],
  can_delegate_plan_management: false,
};

export const UserDialog: FC<UserDialogProps> = () => {
  const {
    editingUser,
    isCreatingNewUser,
    onCreateUser,
    editUser,
    fetchUserUsage,
    onEditingUser,
    createUser,
    onDeletingUser,
  } = useDashboard();
  const isEditing = !!editingUser;
  const isOpen = isCreatingNewUser || isEditing;
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>("");
  const toast = useToast();
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const { userData } = useGetUser();

  const { colorMode } = useColorMode();

  const [usageVisible, setUsageVisible] = useState(false);
  const handleUsageToggle = () => {
    setUsageVisible((current) => !current);
  };

  const form = useForm<FormType>({
    defaultValues: getDefaultValues(),
    resolver: zodResolver(schema),
  });

  const adminsQuery = useQuery<ManagedAdmin[], Error>(
    ["assignable-admins"],
    fetchAssignableAdmins,
    { enabled: isOpen && userData.is_sudo, staleTime: 30000 }
  );
  const capabilitiesQuery = useQuery<AdminCapabilities, Error>(
    ["admin-capabilities"],
    () => fetch("/admin/capabilities"),
    { enabled: isOpen, staleTime: 30000 }
  );
  const accountQuery = useQuery<AccountSummary, Error>(
    ["account-summary"],
    () => fetch("/account/summary"),
    { enabled: isOpen, staleTime: 30000 }
  );
  const formModeAllowed = ["FREE_FORM", "FORM_ONLY", "BOTH"].includes(accountQuery.data?.user_creation_mode || "") && accountQuery.data?.billing_mode !== "USER_CREDIT";
  const customCreateAllowed = isEditing || formModeAllowed;
  const planOnlyEditLocked = Boolean(
    isEditing
    && !userData.is_sudo
    && userData.role !== "OWNER"
    && (accountQuery.data?.user_creation_mode === "PLAN_ONLY" || accountQuery.data?.billing_mode === "USER_CREDIT")
  );
  const restrictedCreate = !isEditing && formModeAllowed && (
    accountQuery.data?.billing_mode === "USED_TRAFFIC" ||
    accountQuery.data?.billing_mode === "ALLOCATED_TRAFFIC"
  );

  useEffect(
    () =>
      useDashboard.subscribe(
        (state) => state.inbounds,
        () => {
          form.reset(getDefaultValues());
        }
      ),
    []
  );

  const [dataLimit, userStatus, ownerAdmin, concurrentUserLimit, requestedUsername] = useWatch({
    control: form.control,
    name: ["data_limit", "status", "owner_admin", "concurrent_user_limit", "username"],
  });
  const selectedOwner = adminsQuery.data?.find(
    (admin) => admin.username === ownerAdmin
  );
  const effectiveCapabilities: AdminCapabilities = selectedOwner
    ? {
      ...unrestrictedCapabilities,
      all_inbounds: selectedOwner.policy.all_inbounds,
      allowed_inbounds: selectedOwner.policy.allowed_inbounds,
      all_user_limits: selectedOwner.policy.all_user_limits,
      allowed_user_limits: selectedOwner.policy.allowed_user_limits,
      allowed_subscription_modes: selectedOwner.policy.allowed_subscription_modes,
      view_full_client_ip: selectedOwner.policy.view_full_client_ip,
      capacity_used: selectedOwner.capacity_used,
      capacity_limit: selectedOwner.policy.device_capacity_limit,
      capacity_remaining: selectedOwner.policy.device_capacity_limit === null
        ? null
        : Math.max(selectedOwner.policy.device_capacity_limit - selectedOwner.capacity_used, 0),
      quota: selectedOwner.quota,
    }
    : capabilitiesQuery.data || unrestrictedCapabilities;
  const allowedInboundTags = effectiveCapabilities.all_inbounds
    ? null
    : effectiveCapabilities.allowed_inbounds;
  const currentOwner = editingUser?.admin?.username || userData.username;
  const requestedOwner = ownerAdmin || userData.username;
  const reclaimedCapacity = isEditing && requestedOwner === currentOwner
    ? editingUser?.concurrent_user_limit || 1
    : 0;
  const assignableCapacity = effectiveCapabilities.capacity_remaining === null
    ? null
    : effectiveCapabilities.capacity_remaining + reclaimedCapacity;
  const requestedCapacity = concurrentUserLimit || 1;
  const lacksCapacity = assignableCapacity !== null && requestedCapacity > assignableCapacity;
  const subscriptionMode: SubscriptionMode = dataLimit && dataLimit > 0
    ? concurrentUserLimit === null
      ? "limited_traffic_unlimited_devices"
      : "limited_traffic_limited_devices"
    : concurrentUserLimit === null
      ? "unlimited_traffic_unlimited_devices"
      : "unlimited_traffic_limited_devices";
  const modeAllowed = effectiveCapabilities.allowed_subscription_modes.includes(subscriptionMode);
  const unlimitedDevicesAllowed = effectiveCapabilities.allowed_subscription_modes.some(
    (mode) => mode.endsWith("_unlimited_devices")
  );

  useEffect(() => {
    if (!isOpen || effectiveCapabilities.all_user_limits) return;
    const current = form.getValues("concurrent_user_limit");
    if (
      !isEditing
      && !(
        current === null
        ? unlimitedDevicesAllowed
        : effectiveCapabilities.allowed_user_limits.includes(current)
      )
    ) {
      form.setValue("concurrent_user_limit", effectiveCapabilities.allowed_user_limits[0] ?? null);
    }
  }, [
    isOpen,
    isEditing,
    effectiveCapabilities.all_user_limits,
    effectiveCapabilities.allowed_user_limits.join(","),
    unlimitedDevicesAllowed,
  ]);

  useEffect(() => {
    if (!isOpen || allowedInboundTags === null) return;
    const current = form.getValues("inbounds");
    Object.entries(current).forEach(([protocol, tags]) => {
      form.setValue(
        `inbounds.${protocol}`,
        tags.filter((tag) => allowedInboundTags.includes(tag))
      );
    });
  }, [isOpen, ownerAdmin, allowedInboundTags?.join(",")]);

  const usageTitle = t("userDialog.total");
  const [usage, setUsage] = useState(createUsageConfig(colorMode, usageTitle));
  const [usageFilter, setUsageFilter] = useState("1m");
  const fetchUsageWithFilter = (query: FilterUsageType) => {
    fetchUserUsage(editingUser!, query).then((data: any) => {
      const labels = [];
      const series = [];
      for (const key in data.usages) {
        series.push(data.usages[key].used_traffic);
        labels.push(data.usages[key].node_name);
      }
      setUsage(createUsageConfig(colorMode, usageTitle, series, labels));
    });
  };

  useEffect(() => {
    if (editingUser) {
      const values = formatUser(editingUser);
      values.owner_admin = editingUser.admin?.username === userData.username
        ? ""
        : editingUser.admin?.username || "";
      form.reset(values);

      fetchUsageWithFilter({
        start: dayjs().utc().subtract(30, "day").format("YYYY-MM-DDTHH:00:00"),
      });
    }
  }, [editingUser, userData.username]);

  const submit = (values: FormType) => {
    setLoading(true);
    const methods = { edited: editUser, created: createUser };
    const method = isEditing ? "edited" : "created";
    setError(null);

    const {
      selected_proxies,
      concurrent_user_limit,
      owner_admin,
      ...rest
    } = values;

    const body = (restrictedCreate
      ? {
        username: values.username,
        data_limit: values.data_limit,
        expire: values.expire,
        note: values.note,
      }
      : {
        ...rest,
        concurrent_user_limit,
        data_limit: values.data_limit,
        proxies: mergeProxies(selected_proxies, values.proxies),
        data_limit_reset_strategy:
          values.data_limit && values.data_limit > 0
            ? values.data_limit_reset_strategy
            : "no_reset",
        status:
          values.status === "active" ||
            values.status === "disabled" ||
            values.status === "on_hold"
            ? values.status
            : "active",
      }) as UserCreate;

    const requestedOwner = owner_admin || userData.username;
    const currentOwner = editingUser?.admin?.username || userData.username;
    const shouldAssignOwner = userData.is_sudo &&
      requestedOwner &&
      requestedOwner !== currentOwner;

    let savedUsername = values.username;
    const request = methods[method](body).then(async (savedUser) => {
      if (savedUser) savedUsername = savedUser.username;
      if (shouldAssignOwner) {
        try {
          await fetch(`/user/${encodeURIComponent(savedUsername)}/set-owner`, {
            method: "PUT",
            query: { admin_username: requestedOwner },
          });
          queryClient.invalidateQueries("admin-management");
          queryClient.invalidateQueries(["assignable-admins"]);
          useDashboard.getState().refetchUsers();
        } catch (assignmentError) {
          if (!isEditing) {
            await fetch(`/user/${encodeURIComponent(savedUsername)}`, {
              method: "DELETE",
            }).catch(() => undefined);
            useDashboard.getState().refetchUsers();
          }
          throw assignmentError;
        }
      }
    });

    request
      .then(() => {
        toast({
          title: t(
            isEditing ? "userDialog.userEdited" : "userDialog.userCreated",
            { username: savedUsername }
          ),
          status: "success",
          isClosable: true,
          position: "top",
          duration: 3000,
        });
        onClose();
      })
      .catch((err) => {
        const detail = err?.response?._data?.detail;
        const fields = detail && typeof detail === "object" ? detail.fields : undefined;
        if (fields && typeof fields === "object") {
          Object.keys(fields).forEach((key) => {
            form.setError(
              key as "proxies" | "username" | "data_limit" | "expire",
              {
                type: "custom",
                message: localizedApiError(err),
              }
            );
          });
        }
        setError(localizedApiError(err));
      })
      .finally(() => {
        setLoading(false);
      });
  };

  const onClose = () => {
    form.reset(getDefaultValues());
    onCreateUser(false);
    onEditingUser(null);
    setError(null);
    setUsageVisible(false);
    setUsageFilter("1m");
  };

  const handleResetUsage = () => {
    useDashboard.setState({ resetUsageUser: editingUser });
  };

  const handleRevokeSubscription = () => {
    useDashboard.setState({ revokeSubscriptionUser: editingUser });
  };

  const disabled = loading;
  const isOnHold = userStatus === "on_hold";

  const [randomUsernameLoading, setrandomUsernameLoading] = useState(false);

  const createRandomUsername = (): string => {
    setrandomUsernameLoading(true);
    let result = "";
    const characters =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    const charactersLength = characters.length;
    let counter = 0;
    while (counter < 6) {
      result += characters.charAt(Math.floor(Math.random() * charactersLength));
      counter += 1;
    }
    return result;
  };

  return (
    <Modal isOpen={isOpen && customCreateAllowed} onClose={onClose} size="6xl" scrollBehavior="inside">
      <ModalOverlay bg="rgba(0, 0, 0, .78)" backdropFilter="blur(8px)" />
      <FormProvider {...form}>
        <ModalContent
          mx="3"
          my="3"
          maxH="calc(100dvh - 24px)"
          overflow="hidden"
          dir={i18n.dir()}
          w="calc(100vw - 24px)"
          maxW="1180px"
          borderRadius={{ base: "12px", md: "18px" }}
          borderTopWidth="2px"
          borderTopColor="gold.400"
          boxShadow="elevated"
          bg="surface.dark"
          sx={{
            "& .chakra-form__label": {
              lineHeight: "1.9",
              marginBottom: "6px",
            },
          }}
        >
          <form onSubmit={form.handleSubmit(submit)} style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0, maxWidth: "100%", overflow: "hidden" }}>
            <ModalHeader
              px={{ base: 4, md: 6 }}
              py={{ base: 4, md: 5 }}
              ps={{ base: 12, md: 14 }}
              pe={{ base: 4, md: 6 }}
              lineHeight="1.7"
              borderBottomWidth="1px"
              borderColor="#33483b"
              bgGradient="linear(to-r, rgba(202,165,61,.08), transparent 48%)"
            >
              <HStack gap={3} align="start">
                <Icon color="gold">
                  {isEditing ? (
                    <EditUserIcon color="white" />
                  ) : (
                    <AddUserIcon color="white" />
                  )}
                </Icon>
                <Box minW={0} flex="1">
                  <HStack spacing={2} flexWrap="wrap">
                    <Text fontWeight="800" fontSize={{ base: "md", sm: "xl" }} lineHeight="1.7">
                      {isEditing
                        ? t("userDialog.editUserTitle")
                        : t("createNewUser")}
                    </Text>
                    <Badge colorScheme="gold" variant="subtle" borderRadius="full" px={2.5} textTransform="none">
                      {isEditing ? t("edit") : t("createUser")}
                    </Badge>
                  </HStack>
                  <Text mt={1} color="gray.400" fontSize="sm" fontWeight="400" lineHeight="1.8">
                    {isEditing ? t("userDialog.editSubtitle") : t("userDialog.createSubtitle")}
                  </Text>
                </Box>
              </HStack>
            </ModalHeader>
            <ModalCloseButton top={3} insetInlineStart={3} insetInlineEnd="auto" isDisabled={disabled} aria-label={t("close")} />
            <ModalBody overflowY="auto" overflowX="hidden" px={{ base: 3, sm: 4, md: 6 }} py={{ base: 4, md: 5 }}>
              <Grid
                templateColumns={{
                  base: "repeat(1, 1fr)",
                  xl: "minmax(0, 1.18fr) minmax(340px, .82fr)",
                }}
                gap={{ base: 4, md: 5 }}
                alignItems="start"
              >
                <GridItem minW={0} p={{ base: 4, md: 5 }} bg="#0d1812" borderWidth="1px" borderColor="#33483b" borderRadius="14px" boxShadow="0 10px 28px rgba(0,0,0,.16)">
                  <VStack justifyContent="space-between" align="stretch" w="full" spacing={5}>
                    <SectionHeader
                      title={t("userDialog.identitySection")}
                      description={t("userDialog.identitySectionHelp")}
                    />
                    <Divider borderColor="#33483b" />
                    <Flex
                      flexDirection="column"
                      gridAutoRows="min-content"
                      w="full"
                    >
                      <Stack direction={{ base: "column", md: "row" }} align="start" w="full" gap={4}>
                        <FormControl mb={3} flex="1" minW={0}>
                          <FormLabel>
                            <Flex gap={2} alignItems={"center"}>
                              {t("username")}
                              {!isEditing && (
                                <IconButton
                                  minW="36px"
                                  h="36px"
                                  variant="ghost"
                                  aria-label={t("userDialog.generateUsername")}
                                  icon={<ReloadIcon className={classNames({ "animate-spin": randomUsernameLoading })} />}
                                  onClick={() => {
                                    const randomUsername =
                                      createRandomUsername();
                                    form.setValue("username", randomUsername);
                                    setTimeout(() => {
                                      setrandomUsernameLoading(false);
                                    }, 350);
                                  }}
                                />
                              )}
                            </Flex>
                          </FormLabel>
                          <Input
                            size="sm"
                            type="text"
                            dir="ltr"
                            borderRadius="8px"
                            error={form.formState.errors.username?.message}
                            disabled={disabled || isEditing}
                            {...form.register("username")}
                          />
                          {!isEditing && accountQuery.data?.user_namespace_prefix && (
                            <FormHelperText dir="ltr" textAlign="start">
                              {accountQuery.data.user_namespace_prefix}_{requestedUsername || t("username")}
                            </FormHelperText>
                          )}
                        </FormControl>
                        {!restrictedCreate && (
                        <FormControl flex={{ base: "1", md: "0 0 210px" }} w={{ base: "full", md: "auto" }}>
                          <FormLabel whiteSpace="normal" lineHeight="1.7">
                            {isEditing ? t("usersTable.status") : t("userDialog.onHold")}
                          </FormLabel>
                          <Controller
                            name="status"
                            control={form.control}
                            render={({ field }) => {
                              const checked = isEditing
                                ? field.value === "active"
                                : field.value === "on_hold";
                              const statusLabel = isEditing
                                ? t(`status.${field.value}`)
                                : checked
                                  ? t("userDialog.onHold")
                                  : t("status.active");
                              return (
                                <HStack
                                  minH="44px"
                                  px={3}
                                  justify="space-between"
                                  borderWidth="1px"
                                  borderColor="gray.600"
                                  borderRadius="8px"
                                  bg="whiteAlpha.50"
                                >
                                  <Text fontSize="sm" color="gray.300" noOfLines={1}>{statusLabel}</Text>
                                  <Switch
                                    aria-label={isEditing ? t("usersTable.status") : t("userDialog.onHold")}
                                    colorScheme="primary"
                                    isChecked={checked}
                                    isDisabled={disabled}
                                    onChange={(event) => {
                                      if (isEditing) {
                                        field.onChange(event.target.checked ? "active" : "disabled");
                                      } else {
                                        field.onChange(event.target.checked ? "on_hold" : "active");
                                      }
                                    }}
                                  />
                                </HStack>
                              );
                            }}
                          />
                        </FormControl>
                        )}
                      </Stack>
                      {userData.is_sudo && (
                        <FormControl mb="10px">
                          <FormLabel>{t("userDialog.ownerAdmin")}</FormLabel>
                          <Select
                            size="sm"
                            disabled={disabled || adminsQuery.isLoading}
                            {...form.register("owner_admin")}
                          >
                            <option value="">{t("userDialog.currentSudoOwner")}</option>
                            {adminsQuery.data?.filter((admin) => !admin.is_sudo).map((admin) => {
                              const isFull = admin.quota.max_users !== null &&
                                admin.quota.current_users >= admin.quota.max_users;
                              return (
                                <option key={admin.username} value={admin.username} disabled={isFull}>
                                  {admin.username} ({admin.quota.current_users} / {admin.quota.max_users ?? t("unlimited")})
                                </option>
                              );
                            })}
                          </Select>
                          <FormHelperText>
                            {selectedOwner
                              ? t("userDialog.ownerCapacity", {
                                current: selectedOwner.capacity_used,
                                max: selectedOwner.policy.device_capacity_limit ?? t("unlimited"),
                              })
                              : t("userDialog.ownerAdminHelp")}
                          </FormHelperText>
                        </FormControl>
                      )}
                      <Divider my={2} borderColor="#33483b" />
                      <SectionHeader
                        title={t("userDialog.limitsSection")}
                        description={t("userDialog.limitsSectionHelp")}
                      />
                      {planOnlyEditLocked && (
                        <Alert status="info" borderRadius="10px" mb={3} alignItems="start">
                          <AlertIcon mt={0.5} />
                          <Text fontSize="sm">حجم، تاریخ پایان و محدودیت دستگاه فقط با تمدید از پلن تغییر می‌کنند.</Text>
                        </Alert>
                      )}
                      <FormControl mb={"10px"}>
                        <FormLabel>{t("userDialog.dataLimit")}</FormLabel>
                        <Controller
                          control={form.control}
                          name="data_limit"
                          render={({ field }) => {
                            return (
                              <Input
                                endAdornment="GB"
                                type="number"
                                dir="ltr"
                                size="sm"
                                borderRadius="6px"
                                onChange={field.onChange}
                                disabled={disabled || planOnlyEditLocked}
                                error={
                                  form.formState.errors.data_limit?.message
                                }
                                value={field.value ? String(field.value) : ""}
                              />
                            );
                          }}
                        />
                      </FormControl>
                      {!restrictedCreate && (
                        <FormControl mb="10px" isInvalid={!!form.formState.errors.concurrent_user_limit}>
                          <FormLabel>{t("userDialog.concurrentUserLimit")}</FormLabel>
                          <Controller
                            control={form.control}
                            name="concurrent_user_limit"
                            render={({ field }) => effectiveCapabilities.all_user_limits ? (
                              <Input
                                type="number"
                                dir="ltr"
                                min={1}
                                step={1}
                                size="sm"
                                borderRadius="6px"
                                disabled={disabled || planOnlyEditLocked}
                                value={field.value ? String(field.value) : ""}
                                onChange={field.onChange}
                                error={form.formState.errors.concurrent_user_limit?.message}
                              />
                            ) : (
                              <Select
                                {...field}
                                value={field.value ?? ""}
                                onChange={(event) => field.onChange(
                                  event.target.value === "" ? null : Number(event.target.value)
                                )}
                                isDisabled={disabled || planOnlyEditLocked}
                                dir="ltr"
                                minH="44px"
                              >
                                <option value="" disabled={!unlimitedDevicesAllowed}>
                                  {unlimitedDevicesAllowed
                                    ? t("unlimited")
                                    : t("userDialog.selectConnectionLimit")}
                                </option>
                                {effectiveCapabilities.allowed_user_limits.map((limit) => (
                                  <option
                                    key={limit}
                                    value={limit}
                                    disabled={assignableCapacity !== null && limit > assignableCapacity}
                                  >
                                    {t("userDialog.connectionLimitOption", { count: limit })}
                                  </option>
                                ))}
                              </Select>
                            )}
                          />
                          <FormHelperText color={lacksCapacity ? "red.300" : "gray.400"}>
                            {t("userDialog.capacityCost", {
                              cost: requestedCapacity,
                              remaining: assignableCapacity ?? t("unlimited"),
                            })}
                          </FormHelperText>
                        </FormControl>
                      )}
                      {!restrictedCreate && !modeAllowed && (
                        <Alert status="warning" borderRadius="10px" mb={3} alignItems="start">
                          <AlertIcon mt={0.5} />
                          <Box>
                            <Text fontWeight="700">{t("userDialog.subscriptionModeForbidden")}</Text>
                            <Text fontSize="sm" mt={1}>{t(`admins.subscriptionMode.${subscriptionMode}`)}</Text>
                          </Box>
                        </Alert>
                      )}
                      {!restrictedCreate && <Collapse
                        in={!!(dataLimit && dataLimit > 0)}
                        animateOpacity
                        style={{ width: "100%" }}
                      >
                        <FormControl pb={3}>
                          <FormLabel>
                            {t("userDialog.periodicUsageReset")}
                          </FormLabel>
                          <Controller
                            control={form.control}
                            name="data_limit_reset_strategy"
                            render={({ field }) => {
                              return (
                                <Select
                                  size="sm"
                                  {...field}
                                  disabled={disabled}
                                  bg={disabled ? "gray.700" : "whiteAlpha.50"}
                                  _dark={{
                                    bg: disabled ? "gray.700" : "whiteAlpha.50",
                                  }}
                                  sx={{
                                    option: {
                                      backgroundColor: "#111d17",
                                      color: "#f1f5f2",
                                    }
                                  }}
                                >
                                  {resetStrategy.map((s) => {
                                    return (
                                      <option key={s.value} value={s.value}>
                                        {t(
                                          "userDialog.resetStrategy" + s.title
                                        )}
                                      </option>
                                    );
                                  })}
                                </Select>
                              );
                            }}
                          />
                        </FormControl>
                      </Collapse>}

                      <FormControl mb={"10px"}>
                        <FormLabel>
                          {isOnHold
                            ? t("userDialog.onHoldExpireDuration")
                            : t("userDialog.expiryDate")}
                        </FormLabel>

                        {isOnHold && (
                          <Controller
                            control={form.control}
                            name="on_hold_expire_duration"
                            render={({ field }) => {
                              return (
                                <Input
                                  endAdornment="Days"
                                  type="number"
                                  dir="ltr"
                                  size="sm"
                                  borderRadius="6px"
                                  onChange={(on_hold) => {
                                    form.setValue("expire", null);
                                    field.onChange({
                                      target: {
                                        value: on_hold,
                                      },
                                    });
                                  }}
                                  disabled={disabled || planOnlyEditLocked}
                                  error={
                                    form.formState.errors
                                      .on_hold_expire_duration?.message
                                  }
                                  value={field.value ? String(field.value) : ""}
                                />
                              );
                            }}
                          />
                        )}
                        {!isOnHold && (
                          <Controller
                            name="expire"
                            control={form.control}
                            render={({ field }) => {
                              function createDateAsUTC(num: number) {
                                return dayjs(
                                  dayjs(num * 1000).utc()
                                  // .format("MMMM D, YYYY") // exception with: dayjs.locale(lng);
                                ).toDate();
                              }
                              const { status, time } = relativeExpiryDate(
                                field.value
                              );
                              return (
                                <>
                                  <ReactDatePicker
                                    locale={i18n.language.toLocaleLowerCase()}
                                    dateFormat={t("dateFormat")}
                                    minDate={new Date()}
                                    selected={
                                      field.value
                                        ? createDateAsUTC(field.value)
                                        : undefined
                                    }
                                    onChange={(date: Date) => {
                                      form.setValue(
                                        "on_hold_expire_duration",
                                        null
                                      );
                                      field.onChange({
                                        target: {
                                          value: date
                                            ? dayjs(
                                              dayjs(date)
                                                .set("hour", 23)
                                                .set("minute", 59)
                                                .set("second", 59)
                                            )
                                              .utc()
                                              .valueOf() / 1000
                                            : 0,
                                          name: "expire",
                                        },
                                      });
                                    }}
                                    customInput={
                                      <Input
                                        size="sm"
                                        type="text"
                                        borderRadius="6px"
                                        clearable
                                        disabled={disabled || planOnlyEditLocked}
                                        error={
                                          form.formState.errors.expire?.message
                                        }
                                      />
                                    }
                                  />
                                  {field.value ? (
                                    <FormHelperText>
                                      {t(status, { time: time })}
                                    </FormHelperText>
                                  ) : (
                                    ""
                                  )}
                                </>
                              );
                            }}
                          />
                        )}
                      </FormControl>

                      <FormControl
                        mb={"10px"}
                        isInvalid={!!form.formState.errors.note}
                      >
                        <FormLabel>{t("userDialog.note")}</FormLabel>
                        <Textarea
                          minH="104px"
                          resize="vertical"
                          bg="whiteAlpha.50"
                          borderColor="gray.600"
                          lineHeight="1.8"
                          {...form.register("note")}
                        />
                        <FormErrorMessage>
                          {form.formState.errors?.note?.message}
                        </FormErrorMessage>
                      </FormControl>
                    </Flex>
                    {error && (
                      <Alert
                        status="error"
                        display={{ base: "none", md: "flex" }}
                      >
                        <AlertIcon />
                        {error}
                      </Alert>
                    )}
                  </VStack>
                </GridItem>
                {!restrictedCreate && <GridItem minW={0} p={{ base: 4, md: 5 }} bg="#0d1812" borderWidth="1px" borderColor="#33483b" borderRadius="14px" boxShadow="0 10px 28px rgba(0,0,0,.16)">
                  <Stack spacing={4} minW={0}>
                    <SectionHeader
                      title={t("userDialog.protocols")}
                      description={t("userDialog.protocolsSectionHelp")}
                    />
                    <Divider borderColor="#33483b" />
                    <FormControl
                      isInvalid={
                        !!form.formState.errors.selected_proxies?.message
                      }
                    >
                    <Controller
                      control={form.control}
                      name="selected_proxies"
                      render={({ field }) => {
                        return (
                          <RadioGroup
                            list={[
                              {
                                title: "vmess",
                                description: t("userDialog.vmessDesc"),
                              },
                              {
                                title: "vless",
                                description: t("userDialog.vlessDesc"),
                              },
                              {
                                title: "trojan",
                                description: t("userDialog.trojanDesc"),
                              },
                              {
                                title: "shadowsocks",
                                description: t("userDialog.shadowsocksDesc"),
                              },
                            ]}
                            disabled={disabled}
                            allowedInboundTags={allowedInboundTags}
                            {...field}
                          />
                        );
                      }}
                    />
                      <FormErrorMessage>
                        {t(
                          form.formState.errors.selected_proxies
                            ?.message as string
                        )}
                      </FormErrorMessage>
                    </FormControl>
                  </Stack>
                </GridItem>}
                {isEditing && editingUser?.used_traffic !== null && usageVisible && (
                  <GridItem pt={2} colSpan={{ base: 1, xl: 2 }} minW={0}>
                    <VStack gap={4}>
                      <UsageFilter
                        defaultValue={usageFilter}
                        onChange={(filter, query) => {
                          setUsageFilter(filter);
                          fetchUsageWithFilter(query);
                        }}
                      />
                      <Box
                        width={{ base: "100%", md: "70%" }}
                        justifySelf="center"
                      >
                        <ReactApexChart
                          options={usage.options}
                          series={usage.series}
                          type="donut"
                        />
                      </Box>
                    </VStack>
                  </GridItem>
                )}
              </Grid>
              {error && (
                <Alert
                  mt="3"
                  status="error"
                  display={{ base: "flex", md: "none" }}
                >
                  <AlertIcon />
                  {error}
                </Alert>
              )}
            </ModalBody>
            <ModalFooter flexShrink={0} mt={0} px={{ base: 3, sm: 4, md: 6 }} py={{ base: 3, md: 4 }} borderTopWidth="1px" borderColor="#33483b" bg="#0b1710">
              <Stack
                justify="space-between"
                align={{ base: "stretch", md: "center" }}
                w="full"
                spacing={2}
                direction={{ base: "column", md: "row" }}
              >
                <HStack
                  justifyContent="flex-start"
                  w={{
                    base: "full",
                    sm: "unset",
                  }}
                  flexWrap="wrap"
                >
                  {isEditing && (
                    <>
                      <Tooltip label={t("delete")} placement="top">
                        <IconButton
                          aria-label={t("delete")}
                          minW="44px"
                          h="44px"
                          colorScheme="red"
                          variant="ghost"
                          onClick={() => {
                            onDeletingUser(editingUser);
                            onClose();
                          }}
                        >
                          <DeleteIcon />
                        </IconButton>
                      </Tooltip>
                      {editingUser?.used_traffic !== null && <Tooltip label={t("userDialog.usage")} placement="top">
                        <IconButton
                          aria-label={t("userDialog.usage")}
                          minW="44px"
                          h="44px"
                          onClick={handleUsageToggle}
                        >
                          <UserUsageIcon />
                        </IconButton>
                      </Tooltip>}
                      {editingUser?.used_traffic !== null && <Button onClick={handleResetUsage} size="sm" whiteSpace="normal" minH="44px" variant="outline" borderColor="#475f50">
                        {t("userDialog.resetUsage")}
                      </Button>}
                      <Button onClick={handleRevokeSubscription} size="sm" whiteSpace="normal" minH="44px" variant="outline" borderColor="#475f50">
                        {t("userDialog.revokeSubscription")}
                      </Button>
                    </>
                  )}
                </HStack>
                <Stack
                  direction={{ base: "column-reverse", sm: "row" }}
                  w="full"
                  maxW={{ md: "420px", base: "full" }}
                  spacing={2}
                  justify="end"
                >
                  <Button
                    type="button"
                    variant="ghost"
                    minH="44px"
                    onClick={onClose}
                    isDisabled={disabled}
                    w={{ base: "full", sm: "auto" }}
                  >
                    {t("cancel")}
                  </Button>
                  <Button
                    type="submit"
                    size="md"
                    px="8"
                    colorScheme="primary"
                    isLoading={loading}
                    isDisabled={disabled || (!restrictedCreate && (lacksCapacity || !modeAllowed))}
                    w={{ base: "full", sm: "auto" }}
                  >
                    {isEditing ? t("userDialog.editUser") : t("createUser")}
                  </Button>
                </Stack>
              </Stack>
            </ModalFooter>
          </form>
        </ModalContent>
      </FormProvider>
    </Modal>
  );
};
