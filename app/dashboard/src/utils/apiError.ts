import i18n from "locales/i18n";

type ApiErrorDetail = {
  part?: unknown;
  total?: unknown;
  code?: unknown;
  error_code?: unknown;
  message_fa?: unknown;
  request_id?: unknown;
  field?: unknown;
  fields?: unknown;
  correlation_id?: unknown;
  operation_id?: unknown;
};

const messages: Record<string, string> = {
  subscription_mode_forbidden: "اجازه ساخت این نوع اشتراک را ندارید.",
  plan_only_direct_edit_forbidden: "این کاربر فقط از طریق پلن قابل تغییر است.",
  backup_mixed_sets: "فقط قطعه‌های یک بکاپ را انتخاب کنید.",
  backup_duplicate_part: "یک قطعه بیش از یک‌بار انتخاب شده است.",
  backup_part_number_invalid: "شماره یا تعداد قطعه‌های بکاپ معتبر نیست.",
  backup_file_count_invalid: "بین ۱ تا ۱۲۸ فایل بکاپ انتخاب کنید.",
  backup_file_type_invalid: "آرشیو ZIP یا تمام قطعه‌های همان بکاپ را انتخاب کنید.",
  backup_checksum_mismatch: "بکاپ آسیب دیده است؛ فایل‌ها را دوباره دریافت کنید.",
  backup_archive_invalid: "بکاپ ناقص یا نامعتبر است؛ تمام قطعه‌ها را بررسی کنید.",
  backup_too_large: "حجم کل بکاپ نباید بیشتر از ۲ گیگابایت باشد.",
  offline_restore_required: "بازیابی نیازمند توقف سرویس و اجرای روش آفلاین است.",
};

export const safeUserMessage = (value: unknown): string | null => {
  if (typeof value !== "string" || value.length > 500 || !/[\u0600-\u06ff]/.test(value)) return null;
  if (/traceback|exception|error|sqlalchemy|mysql|pymysql|xray|\b(select|insert|update|delete|constraint)\b|[a-z]+_[a-z_]+/i.test(value)) return null;
  return value.trim() || null;
};

const safeIdentifier = (value: unknown): string | null => {
  if (typeof value !== "string" || !/^[A-Za-z0-9_.:-]{1,128}$/.test(value)) return null;
  return value;
};

export const localizedApiError = (error: unknown): string => {
  const candidate = error as any;
  const detail = (candidate?.data?.detail || candidate?.response?._data?.detail) as ApiErrorDetail | string | undefined;
  const status = Number(candidate?.status || candidate?.statusCode || candidate?.response?.status || 0);
  if (detail && typeof detail === "object") {
    const code = safeIdentifier(detail.error_code) || safeIdentifier(detail.code);
    if (code === "backup_missing_part" && Number.isInteger(detail.part) && Number.isInteger(detail.total)) {
      return `بکاپ ناقص است؛ قطعه ${detail.part} از ${detail.total} موجود نیست.`;
    }
    if (code && messages[code]) return messages[code];
    const friendly = safeUserMessage(detail.message_fa);
    if (friendly) return friendly;
    if (code) {
      const key = `errors.codes.${code}`;
      const translated = i18n.t(key, { defaultValue: "" });
      if (translated && translated !== key) return translated;
      return i18n.t("errors.fallback");
    }
    const correlation = safeIdentifier(detail.request_id) || safeIdentifier(detail.correlation_id) || safeIdentifier(detail.operation_id);
    if (correlation) return i18n.t("errors.fallbackWithReference", { reference: correlation });
  }
  const friendly = safeUserMessage(detail);
  if (friendly) return friendly;
  return status ? i18n.t("errors.fallbackWithStatus", { status }) : i18n.t("errors.fallback");
};
