import {
  BoxProps,
  Button,
  chakra,
  Grid,
  GridItem,
  HStack,
  IconButton,
  Input,
  InputGroup,
  InputLeftElement,
  InputRightElement,
  Select,
  Spinner,
  Text,
} from "@chakra-ui/react";
import {
  ArrowPathIcon,
  MagnifyingGlassIcon,
  XMarkIcon,
} from "@heroicons/react/24/outline";
import classNames from "classnames";
import { useDashboard } from "contexts/DashboardContext";
import useGetUser from "hooks/useGetUser";
import debounce from "lodash.debounce";
import React, { FC, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "react-query";
import { Link } from "react-router-dom";
import { fetch } from "service/http";
import { AccountSummary, AdminCapabilities } from "types/Admin";
import { CreateUserFromPlan } from "./CreateUserFromPlan";

const iconProps = {
  baseStyle: {
    w: 4,
    h: 4,
  },
};

const SearchIcon = chakra(MagnifyingGlassIcon, iconProps);
const ClearIcon = chakra(XMarkIcon, iconProps);
export const ReloadIcon = chakra(ArrowPathIcon, iconProps);

export type FilterProps = {} & BoxProps;
type AdminOption = { username: string };

const fetchAdminOptions = () =>
  fetch<AdminOption[]>("/admins", { query: { limit: 1000 } });

const setSearchField = debounce((search: string) => {
  useDashboard.getState().onFilterChange({
    ...useDashboard.getState().filters,
    offset: 0,
    search,
  });
}, 300);

export const Filters: FC<FilterProps> = ({ ...props }) => {
  const { loading, filters, onFilterChange, refetchUsers, onCreateUser } =
    useDashboard();
  const { t, i18n } = useTranslation();
  const { userData } = useGetUser();
  const account = useQuery<AccountSummary, Error>("account-summary", () => fetch("/account/summary"));
  const capabilities = useQuery<AdminCapabilities, Error>(["admin-capabilities", userData.username], () => fetch("/admin/capabilities"));
  const canManageAdmins = Boolean(capabilities.data?.can_manage_admins);
  const accountActive = account.data?.account_status === "ACTIVE";
  const adminOptions = useQuery<AdminOption[], Error>(
    ["user-filter-admins"],
    fetchAdminOptions,
    { enabled: canManageAdmins, staleTime: 30000 }
  );
  const [search, setSearch] = useState("");
  const [planCreateOpen, setPlanCreateOpen] = useState(false);
  const controlStyle = {
    bg: "rgba(2, 6, 23, .58)",
    color: "gray.100",
    borderColor: "rgba(148, 163, 184, .2)",
    _hover: { borderColor: "rgba(34, 197, 94, .42)" },
    _focusVisible: {
      borderColor: "green.400",
      boxShadow: "0 0 0 2px rgba(34, 197, 94, .2)",
    },
  } as const;
  const onChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearch(e.target.value);
    setSearchField(e.target.value);
  };
  const clear = () => {
    setSearch("");
    onFilterChange({
      ...filters,
      offset: 0,
      search: "",
    });
  };
  const changeSort = (e: React.ChangeEvent<HTMLSelectElement>) => {
    onFilterChange({ sort: e.target.value, offset: 0 });
  };
  const changeStatus = (e: React.ChangeEvent<HTMLSelectElement>) => {
    onFilterChange({
      status: e.target.value
        ? (e.target.value as typeof filters.status)
        : undefined,
      offset: 0,
    });
  };
  const changeAdmin = (e: React.ChangeEvent<HTMLSelectElement>) => {
    onFilterChange({ admin: e.target.value || undefined, offset: 0 });
  };
  return (
    <Grid
      id="filters"
      dir={i18n.dir()}
      templateColumns={{
        lg: "repeat(3, 1fr)",
        md: "repeat(4, 1fr)",
        base: "repeat(1, 1fr)",
      }}
      position="relative"
      px={{ base: 4, md: 5 }}
      rowGap={4}
      gap={{
        lg: 4,
        base: 0,
      }}
      bg="transparent"
      py={5}
      zIndex="docked"
      {...props}
    >
      <GridItem colSpan={{ base: 1, md: 2, lg: 1 }} order={{ base: 2, md: 1 }}>
        <InputGroup>
          <InputLeftElement pointerEvents="none" children={<SearchIcon />} />
          <Input
            placeholder={t("search")}
            value={search}
            onChange={onChange}
            {...controlStyle}
          />

          <InputRightElement>
            {loading && <Spinner size="xs" />}
            {filters.search && filters.search.length > 0 && (
              <IconButton
                onClick={clear}
                aria-label="clear"
                size="xs"
                variant="ghost"
              >
                <ClearIcon />
              </IconButton>
            )}
          </InputRightElement>
        </InputGroup>
      </GridItem>
      <GridItem colSpan={{ base: 1, md: 2 }} order={{ base: 1, md: 2 }}>
        <HStack justifyContent="flex-end" alignItems="center" h="full" w="full">
          <IconButton
            aria-label="refresh users"
            isDisabled={loading}
            onClick={refetchUsers}
            size="sm"
            variant="outline"
            minW="44px"
            h="44px"
            color="gray.300"
            bg="rgba(2, 6, 23, .5)"
            borderColor="rgba(148, 163, 184, .2)"
            _hover={{ color: "green.200", borderColor: "green.500" }}
          >
            <ReloadIcon
              className={classNames({
                "animate-spin": loading,
              })}
            />
          </IconButton>
          {account.isLoading && <Button size="sm" minH="44px" px={5} isDisabled isLoading>بررسی دسترسی</Button>}
          {accountActive && account.data?.billing_mode !== "USER_CREDIT" && ["FREE_FORM", "FORM_ONLY", "BOTH"].includes(account.data?.user_creation_mode || "") && <Button
            colorScheme="primary"
            size="sm"
            onClick={() => onCreateUser(true)}
            px={5}
            minH="44px"
            boxShadow="0 0 18px rgba(34, 197, 94, .18)"
          >
            {t("createUser")}
          </Button>}
          {accountActive && (account.data?.billing_mode === "USER_CREDIT" || ["PLAN_ONLY", "BOTH"].includes(account.data?.user_creation_mode || "")) && <Button
            onClick={() => setPlanCreateOpen(true)}
            colorScheme="primary"
            size="sm"
            px={5}
            minH="44px"
          >
            ساخت کاربر از پلن
          </Button>}
        </HStack>
      </GridItem>
      <GridItem
        colSpan={{ base: 1, md: 4, lg: 3 }}
        order={3}
        borderTop="1px solid"
        borderColor="gray.200"
        _dark={{ borderColor: "whiteAlpha.200" }}
        pt={3}
      >
        <HStack spacing={3} justify="flex-end" flexWrap="wrap" fontSize="sm">
          <Text
            color="gray.600"
            _dark={{ color: "gray.300" }}
            fontWeight="medium"
            whiteSpace="nowrap"
            w={{ base: "full", sm: "auto" }}
          >
            {t("usersTable.organizeUsers")}
          </Text>
          <Select
            aria-label={t("usersTable.filterStatus")}
            value={filters.status || ""}
            onChange={changeStatus}
            size="sm"
            rounded="md"
            w={{ base: "full", sm: "170px" }}
            {...controlStyle}
            sx={{ option: { background: "#080f19", color: "#f8fafc" } }}
          >
            <option value="">{t("usersTable.allStatuses")}</option>
            <option value="active">{t("active")}</option>
            <option value="on_hold">{t("on_hold")}</option>
            <option value="disabled">{t("disabled")}</option>
            <option value="limited">{t("limited")}</option>
            <option value="expired">{t("expired")}</option>
          </Select>
          {canManageAdmins && (
            <Select
              aria-label={t("usersTable.filterAdmin")}
              value={filters.admin || ""}
              onChange={changeAdmin}
              size="sm"
              rounded="md"
              w={{ base: "full", sm: "180px" }}
              {...controlStyle}
              sx={{ option: { background: "#080f19", color: "#f8fafc" } }}
              isDisabled={adminOptions.isLoading}
            >
              <option value="">{t("usersTable.allAdmins")}</option>
              {adminOptions.data?.map((admin) => (
                <option key={admin.username} value={admin.username}>
                  {admin.username}
                </option>
              ))}
            </Select>
          )}
          <Select
            aria-label={t("usersTable.sortBy")}
            value={filters.sort}
            onChange={changeSort}
            size="sm"
            rounded="md"
            w={{ base: "full", sm: "220px" }}
            {...controlStyle}
            sx={{ option: { background: "#080f19", color: "#f8fafc" } }}
          >
            <option value="-created_at">{t("usersTable.newestFirst")}</option>
            <option value="created_at">{t("usersTable.oldestFirst")}</option>
            <option value="username">{t("usersTable.usernameAZ")}</option>
            <option value="-username">{t("usersTable.usernameZA")}</option>
            <option value="admin">{t("usersTable.adminAZ")}</option>
            <option value="-admin">{t("usersTable.adminZA")}</option>
            <option value="-used_traffic">
              {t("usersTable.usageHighLow")}
            </option>
            <option value="used_traffic">{t("usersTable.usageLowHigh")}</option>
            <option value="expire">{t("usersTable.expireSoon")}</option>
            <option value="-expire">{t("usersTable.expireLate")}</option>
          </Select>
        </HStack>
      </GridItem>
      <CreateUserFromPlan isOpen={planCreateOpen} onClose={() => setPlanCreateOpen(false)} />
    </Grid>
  );
};
