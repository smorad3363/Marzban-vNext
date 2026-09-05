import { Button, ButtonGroup, HStack, IconButton, Input, Text, Tooltip, useToast } from "@chakra-ui/react";
import { ArrowUpTrayIcon, TrashIcon } from "@heroicons/react/24/outline";
import { ChangeEvent, FC, useRef } from "react";
import { useMutation, useQueryClient } from "react-query";
import { fetch } from "service/http";
import { BrandingResponse } from "types/Admin";
import { CurrentAdminQueryKey } from "hooks/useGetUser";
import { localizedApiError } from "utils/apiError";

type Props = {
  theme: "heisenberg" | "black_gold";
  hasLogo: boolean;
};

export const BrandingControls: FC<Props> = ({ theme, hasLogo }) => {
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const queryClient = useQueryClient();
  const refresh = (data: BrandingResponse) => {
    document.documentElement.dataset.panelTheme = data.dashboard_theme;
    queryClient.setQueryData(CurrentAdminQueryKey, (current: any) => ({ ...current, ...data }));
  };
  const themeMutation = useMutation(
    (dashboard_theme: Props["theme"]) => fetch<BrandingResponse>("/branding", { method: "PUT", body: { dashboard_theme } }),
    { onSuccess: refresh, onError: (error) => { toast({ title: "تم ذخیره نشد", description: localizedApiError(error), status: "error" }); } }
  );
  const logoMutation = useMutation(
    (body: FormData) => fetch<BrandingResponse>("/branding/logo", { method: "POST", body }),
    { onSuccess: refresh, onError: (error) => { toast({ title: "لوگو ذخیره نشد", description: localizedApiError(error), status: "error" }); } }
  );
  const removeMutation = useMutation(
    () => fetch<BrandingResponse>("/branding/logo", { method: "DELETE" }),
    { onSuccess: refresh, onError: (error) => { toast({ title: "لوگو حذف نشد", description: localizedApiError(error), status: "error" }); } }
  );
  const upload = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const body = new FormData();
    body.append("logo", file);
    logoMutation.mutate(body);
    event.target.value = "";
  };

  return (
    <HStack px={2} spacing={2} justify="space-between">
      <ButtonGroup size="xs" isAttached variant="outline">
        <Button aria-pressed={theme === "heisenberg"} variant={theme === "heisenberg" ? "solid" : "outline"} onClick={() => themeMutation.mutate("heisenberg")}>آبی</Button>
        <Button aria-pressed={theme === "black_gold"} variant={theme === "black_gold" ? "solid" : "outline"} colorScheme="yellow" onClick={() => themeMutation.mutate("black_gold")}>طلایی</Button>
      </ButtonGroup>
      <HStack spacing={1}>
        <Input ref={fileRef} display="none" type="file" accept="image/png,image/jpeg,image/webp" onChange={upload} />
        <Tooltip label="انتخاب لوگو (PNG، JPG یا WebP)">
          <IconButton aria-label="انتخاب لوگو" size="xs" variant="ghost" isLoading={logoMutation.isLoading} icon={<ArrowUpTrayIcon width={15} />} onClick={() => fileRef.current?.click()} />
        </Tooltip>
        {hasLogo && <Tooltip label="بازگشت به لوگوی پیش‌فرض"><IconButton aria-label="حذف لوگوی سفارشی" size="xs" variant="ghost" colorScheme="red" isLoading={removeMutation.isLoading} icon={<TrashIcon width={15} />} onClick={() => removeMutation.mutate()} /></Tooltip>}
      </HStack>
      <Text srOnly>شخصی‌سازی ظاهر پنل</Text>
    </HStack>
  );
};
