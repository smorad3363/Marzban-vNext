export type SubscriptionMode =
  | "limited_traffic_unlimited_devices"
  | "unlimited_traffic_limited_devices"
  | "limited_traffic_limited_devices"
  | "unlimited_traffic_unlimited_devices";

export type AdminPolicy = {
  billing_mode: "LEGACY_COMPAT" | "SEAT_CREDIT" | "USED_TRAFFIC" | "ALLOCATED_TRAFFIC" | "USER_CREDIT";
  money_billing_enabled: boolean;
  money_balance_toman: number;
  used_traffic_price_per_gib_toman: number | null;
  total_traffic: number | null;
  expiry_date: string | null;
  user_limit: number | null;
  max_users: number | null;
  device_capacity_limit: number | null;
  admin_traffic_warning_percent: number;
  sudo_traffic_warning_percent: number;
  all_inbounds: boolean;
  allowed_inbounds: string[];
  all_user_limits: boolean;
  allowed_user_limits: number[];
  allowed_subscription_modes: SubscriptionMode[];
  view_full_client_ip: boolean;
  max_user_duration_days: number | null;
  calculate_volume: "used_traffic" | "created_traffic";
  prevent_user_creation: boolean;
  prevent_user_deletion: boolean;
  prevent_user_reset: boolean;
  prevent_revoke_subscription: boolean;
  prevent_unlimited_traffic: boolean;
};

export type ManagedAdmin = {
  id: number;
  username: string;
  is_sudo: boolean;
  role: "OWNER" | "ADMIN";
  parent_admin_id: number | null;
  external_api_enabled: boolean;
  telegram_id: number | null;
  phone: string | null;
  discord_webhook: string | null;
  users_usage: number | null;
  user_count: number;
  capacity_used: number;
  policy: AdminPolicy;
  quota: AdminQuotaSummary;
  plan_category_ids: number[];
  plan_prices?: Array<{ plan_id: number; price_toman: number }>;
  user_creation_mode: "FREE_FORM" | "FORM_ONLY" | "PLAN_ONLY" | "BOTH";
  can_manage_plans: boolean;
  dashboard_theme: "heisenberg" | "black_gold";
  logo_url: string | null;
  account_status: "ACTIVE" | "SUSPENDED" | "DISABLED";
  parent_username: string | null;
  active_owner_freeze_event_id: number | null;
  trial_quota: number;
  trial_quota_limit: number;
  trials_used: number;
  can_create_admins: boolean;
  can_delegate_admin_creation: boolean;
  can_create_allocated_children: boolean;
  admin_creation_limit: number | null;
  admin_creations_used: number;
  delegated_admin_creation_limit: number;
  admin_creation_remaining: number | null;
};

export type AdminQuotaSummary = {
  current_users: number;
  lifetime_consumed_traffic: number;
  lifetime_created_traffic: number;
  max_users: number | null;
  remaining_user_slots: number | null;
  credit_limit: number | null;
  credit_used: number;
  credit_remaining: number | null;
  credit_usage_percent: number | null;
  credit_calculation_mode: "used_traffic" | "created_traffic";
  operation_allowance_remaining: number | null;
  admin_warning_percent: number;
  sudo_warning_percent: number;
  admin_warning_active: boolean;
  sudo_warning_active: boolean;
};

export type ManagedAdminList = {
  admins: ManagedAdmin[];
  total: number;
  offset: number;
  limit: number;
};

export type ManagedAdminPayload = Omit<
  ManagedAdmin,
  "id" | "parent_admin_id" | "external_api_enabled" | "users_usage" | "user_count" | "capacity_used" | "quota" |
  "dashboard_theme" | "logo_url" | "admin_creations_used" | "delegated_admin_creation_limit" | "admin_creation_remaining"
  | "account_status" | "parent_username" | "active_owner_freeze_event_id" | "trial_quota" | "trial_quota_limit" | "trials_used"
> & {
  password?: string;
  initial_money_credit_toman: number;
};

export type HierarchyAdminNode = {
  id: number;
  username: string;
  role: "OWNER" | "ADMIN";
  parent_admin_id: number | null;
  depth: number;
  external_api_enabled: boolean;
  account_status: "ACTIVE" | "SUSPENDED" | "DISABLED";
  total_traffic: number | null;
  delegated_traffic: number;
  own_spend: number;
  available_traffic: number | null;
  renewal_enabled: boolean;
  renewal_remaining: number | null;
  trial_quota: number;
  trials_used: number;
  referral_referrer_admin_id: number | null;
  referral_rate_bps: number | null;
  active_owner_freeze_event_id: number | null;
  billing_mode: AdminPolicy["billing_mode"];
  can_create_admins: boolean;
  can_delegate_admin_creation: boolean;
  can_create_allocated_children: boolean;
  admin_creation_limit: number | null;
  admin_creations_used: number;
  delegated_admin_creation_limit: number;
  admin_creation_remaining: number | null;
  children: HierarchyAdminNode[];
};

export type AccountSummary = {
  username: string;
  user_namespace_prefix: string;
  role: "OWNER" | "ADMIN";
  account_status: "ACTIVE" | "SUSPENDED" | "DISABLED";
  suspended_reason: string | null;
  suspended_at: string | null;
  own_users: number;
  subtree_users: number;
  total_traffic: number | null;
  delegated_traffic: number;
  own_spend: number;
  available_traffic: number | null;
  renewal_enabled: boolean;
  renewal_remaining: number | null;
  billing_mode: "LEGACY_COMPAT" | "SEAT_CREDIT" | "USED_TRAFFIC" | "ALLOCATED_TRAFFIC" | "USER_CREDIT";
  money_billing_enabled: boolean;
  money_balance_toman: number;
  used_traffic_price_per_gib_toman: number | null;
  user_creation_mode: "FREE_FORM" | "FORM_ONLY" | "PLAN_ONLY" | "BOTH";
  can_manage_plans: boolean;
  trial_quota: number;
  trials_used: number;
  can_create_admins: boolean;
  can_delegate_admin_creation: boolean;
  can_create_allocated_children: boolean;
  admin_creation_limit: number | null;
  admin_creations_used: number;
  delegated_admin_creation_limit: number;
  admin_creation_remaining: number | null;
};

export type UserPlanVersion = {
  price_toman: number;
  data_limit: number;
  duration_days: number;
  concurrent_user_limit: number | null;
  reset_strategy: "no_reset" | "day" | "week" | "month" | "year";
  renewal_volume_strategy: "replace";
  renewal_time_strategy: "extend_max";
  inbounds: string[];
  hosts: Record<string, number[]>;
};

export type PlanNetworkHostOption = {
  id: number;
  remark: string;
};

export type PlanNetworkOption = {
  tag: string;
  protocol: string;
  network: string;
  tls: string;
  port?: number;
  hosts: PlanNetworkHostOption[];
};

export type UserPlan = {
  id: number;
  owner_admin_id: number;
  name: string;
  description: string | null;
  category_id: number | null;
  category_name: string | null;
  current_version_id: number;
  version_number: number;
  archived_at: string | null;
  version: UserPlanVersion;
  allowed_admin_ids: number[];
  include_subtree: boolean;
  is_trial: boolean;
  effective_price_toman: number;
  base_price_toman: number | null;
};

export type PlanCategory = {
  id: number;
  owner_admin_id: number;
  name: string;
  description: string | null;
  archived_at: string | null;
  plan_count: number;
};

export type AdminCapabilities = {
  hierarchy_enabled: boolean;
  all_inbounds: boolean;
  allowed_inbounds: string[];
  all_user_limits: boolean;
  allowed_user_limits: number[];
  allowed_subscription_modes: SubscriptionMode[];
  view_full_client_ip: boolean;
  capacity_used: number;
  capacity_limit: number | null;
  capacity_remaining: number | null;
  quota: AdminQuotaSummary;
  can_manage_admins: boolean;
  can_create_admins: boolean;
  can_delegate_admin_creation: boolean;
  can_create_allocated_children: boolean;
  admin_creation_limit: number | null;
  admin_creations_used: number;
  delegated_admin_creation_limit: number;
  admin_creation_remaining: number | null;
  allowed_child_roles: Array<"ADMIN">;
  allowed_child_billing_modes: AdminPolicy["billing_mode"][];
  allowed_child_user_creation_modes: Array<"FREE_FORM" | "PLAN_ONLY">;
  can_delegate_plan_management: boolean;
};

export type BrandingResponse = {
  dashboard_theme: "heisenberg" | "black_gold";
  logo_url: string | null;
};

export type SystemBranding = {
  panel_name: string;
  login_title: string;
  description: string | null;
  logo_url: string | null;
  favicon_url: string | null;
};
