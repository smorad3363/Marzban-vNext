export type Status =
  | "active"
  | "disabled"
  | "limited"
  | "expired"
  | "on_hold"
  | "error"
  | "connecting"
  | "connected";
export type ProxyKeys = ("vmess" | "vless" | "trojan" | "shadowsocks")[];
export type ProxyType = {
  vmess?: {
    id?: string;
  };
  vless?: {
    id?: string;
    flow?: string;
  };
  trojan?: {
    password?: string;
  };
  shadowsocks?: {
    password?: string;
    method?: string;
  };
};

export type DataLimitResetStrategy =
  | "no_reset"
  | "day"
  | "week"
  | "month"
  | "year";

export type UserInbounds = {
  [key: string]: string[];
};

export type NextPlan = {
  data_limit: number | null;
  expire: number | null;
  add_remaining_traffic: boolean;
  fire_on_either: boolean;
};

export type UsageReset = {
  used_traffic: number;
  reset_at: string;
};

export type User = {
  id: number;
  proxies: ProxyType;
  expire: number | null;
  data_limit: number | null;
  data_limit_reset_strategy: DataLimitResetStrategy;
  on_hold_expire_duration: number | null;
  lifetime_used_traffic: number | null;
  username: string;
  used_traffic: number | null;
  status: Status;
  links: string[];
  subscription_url: string;
  inbounds: UserInbounds;
  note: string;
  online_at: string | null;
  created_at: string;
  sub_updated_at: string | null;
  sub_last_user_agent: string | null;
  admin: {
    username: string;
  } | null;
  next_plan: NextPlan | null;
  reset_history: UsageReset[];
  concurrent_user_limit?: number | null;
  device_limit_state?: import("./DeviceLimit").DeviceLimitState | null;
};

export type UserCreate = Pick<
  User,
  | "inbounds"
  | "proxies"
  | "expire"
  | "data_limit"
  | "data_limit_reset_strategy"
  | "on_hold_expire_duration"
  | "username"
  | "status"
  | "note"
>;

export type UserApi = {
  id?: number | null;
  discord_webook: string;
  is_sudo: boolean;
  role?: "OWNER" | "ADMIN" | null;
  parent_admin_id?: number | null;
  external_api_enabled?: boolean;
  telegram_id: number | string;
  username: string;
  dashboard_theme?: "heisenberg" | "black_gold";
  logo_url?: string | null;
};

export type UseGetUserReturn = {
  userData: UserApi;
  getUserIsPending: boolean;
  getUserIsSuccess: boolean;
  getUserIsError: boolean;
  getUserError: Error | null;
};

export type BulkUserOperation =
  | "activate"
  | "deactivate"
  | "add_data"
  | "subtract_data"
  | "add_days"
  | "subtract_days"
  | "add_data_and_days"
  | "delete";

export type BulkTargetScope =
  | "ALL_USERS"
  | "SELECTED_ADMINS_DIRECT"
  | "SELECTED_ADMINS_SUBTREE";

export type BulkTargetResult = {
  target_type: "USER" | "ADMIN";
  target_id: number;
  target_username: string;
  owner_admin_id: number | null;
  status: "PENDING" | "SUCCESS" | "FAILED" | "SKIPPED";
  attempts: number;
  retryable: boolean;
  error_code: string | null;
  error_message: string | null;
  result_details: Record<string, unknown> | null;
};

export type BulkJobResponse = {
  operation_id: string;
  job_kind: "USER" | "ADMIN_CREDIT";
  operation: string;
  target_scope: BulkTargetScope | "SELECTED_ADMINS_DIRECT" | null;
  selected_admin_ids: number[];
  status: string;
  total: number;
  success: number;
  failed: number;
  skipped: number;
  pending: number;
  has_more: boolean;
  report_has_more: boolean;
  next_target_cursor: number | null;
  targets: BulkTargetResult[];
};

export type BulkPreviewResponse = {
  target_scope: BulkTargetScope | "SELECTED_ADMINS_DIRECT";
  selected_admin_ids: number[];
  resolved_target_count: number;
  sample_targets: string[];
};

export type BulkSelectionPreview = {
  user_count: number;
  traffic_change: number;
  duration_change_days: number;
  status_change: string | null;
  cost_toman: number;
  usernames: string[];
};

export type BulkSelectionResponse = {
  operation_id: string;
  success: number;
  failed: number;
  results: Array<{
    user_id: number;
    username: string;
    status: "SUCCESS" | "FAILED";
    reason: string | null;
  }>;
};
