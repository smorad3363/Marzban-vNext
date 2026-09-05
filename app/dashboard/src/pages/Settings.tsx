import {
  Alert, AlertIcon, Badge, Box, Button, Card, FormControl, FormLabel, Grid,
  Heading, HStack, Input, Select, SimpleGrid, Stack, Switch, Text, Textarea,
  Skeleton, useToast,
} from "@chakra-ui/react";
import { AppShell } from "components/AppShell";
import { ChangeEvent, FC, FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery } from "react-query";
import { fetch } from "service/http";
import { SystemBranding } from "types/Admin";
import { queryClient } from "utils/react-query";
import { localizedApiError } from "utils/apiError";

type Pricing = { price_per_gib_toman: number; allow_unlimited_duration: boolean; duration_presets: Array<{ duration_days: number; multiplier: number; enabled: boolean }> };
type BackupSettings = { enabled: boolean; destination: "LOCAL" | "TELEGRAM" | "EMAIL" | "TELEGRAM_EMAIL"; schedule: string; retention_count: number; telegram_bot_token?: string | null; telegram_chat_id?: string | null; smtp_host?: string | null; smtp_port?: number | null; smtp_username?: string | null; smtp_password?: string | null; smtp_use_tls: boolean; email_from?: string | null; email_to?: string | null; telegram_configured: boolean; smtp_configured: boolean };
type AccessGroupSummary = { id: number; name: string; archived_at: string | null; active_user_count: number; inbounds: string[] };
const sectionNames = ["General", "Users", "Admin Policies", "Plans & Pricing", "Access Groups", "Nodes", "Backup & Restore", "Branding", "System"];

const Section: FC<{ id: string; title: string; description: string; children: React.ReactNode }> = ({ id, title, description, children }) => <Card tabIndex={-1} id={id} p={{ base: 4, md: 6 }} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="var(--radius-panel)" boxShadow="var(--shadow-panel)">
  <Heading size="md">{title}</Heading><Text mt={1} color="gray.600" _dark={{ color: "gray.400" }} fontSize="sm">{description}</Text><Box mt={5}>{children}</Box>
</Card>;

export const Settings: FC = () => {
  const toast = useToast();
  const groupsQuery = useQuery<AccessGroupSummary[]>("access-groups", () => fetch("/access-groups"));
  const [uploading, setUploading] = useState(false);
  const brandingQuery = useQuery<SystemBranding>("system-branding", () => fetch("/branding/public"));
  const pricingQuery = useQuery<Pricing>("owner-pricing", () => fetch("/owner/pricing"), { refetchOnWindowFocus: false });
  const backupQuery = useQuery<BackupSettings>("backup-settings", () => fetch("/owner/backups/settings"), { refetchOnWindowFocus: false });
  const [branding, setBranding] = useState<SystemBranding | null>(null);
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [backup, setBackup] = useState<BackupSettings | null>(null);
  const [restoreFiles, setRestoreFiles] = useState<File[]>([]);
  const [validation, setValidation] = useState<{ token: string; manifest: Record<string, unknown> } | null>(null);
  const [validating, setValidating] = useState(false);
  useEffect(() => { if (brandingQuery.data) setBranding((current) => current ?? brandingQuery.data!); }, [brandingQuery.data]);
  useEffect(() => { if (pricingQuery.data) setPricing((current) => current ?? pricingQuery.data!); }, [pricingQuery.data]);
  useEffect(() => { if (backupQuery.data) setBackup((current) => current ?? backupQuery.data!); }, [backupQuery.data]);
  const success = (title: string) => { toast({ title, status: "success", duration: 2500 }); };
  const failure = (error: unknown) => { toast({ title: "Operation failed", description: localizedApiError(error), status: "error" }); };

  const saveBranding = useMutation(() => fetch<SystemBranding>("/branding/system", { method: "PUT", body: branding }), { onSuccess: (value) => { setBranding(value); queryClient.setQueryData("system-branding", value); success("Branding saved"); }, onError: failure });
  const uploadBrand = async (kind: "logo" | "favicon", event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]; if (!file || uploading) return;
    setUploading(true);
    const body = new FormData(); body.append(kind, file);
    try { const value = await fetch<SystemBranding>(`/branding/system/${kind}`, { method: "POST", body }); setBranding(value); queryClient.setQueryData("system-branding", value); success(`${kind} uploaded`); } catch (error) { failure(error); } finally { setUploading(false); }
  };
  const savePricing = useMutation(() => fetch<Pricing>("/owner/pricing", { method: "PUT", body: pricing }), { onSuccess: (value) => { setPricing(value); success("Pricing policy saved"); }, onError: failure });
  const saveBackup = useMutation(() => fetch<BackupSettings>("/owner/backups/settings", { method: "PUT", body: backup }), { onSuccess: (value) => { setBackup(value); success("Backup settings saved"); }, onError: failure });
  const createBackup = useMutation(() => fetch("/owner/backups", { method: "POST" }), { onSuccess: () => success("Backup created"), onError: failure });
  const validateRestore = async (files: File[]) => {
    if (!files.length || validating) return;
    setRestoreFiles(files);
    const body = new FormData(); files.forEach((file) => body.append("backups", file));
    setValidating(true); setValidation(null);
    try { const value = await fetch<{ validation_token: string; manifest: Record<string, unknown> }>("/owner/backups/validate", { method: "POST", body }); setValidation({ token: value.validation_token, manifest: value.manifest }); success("Backup validated"); } catch (error) { setValidation(null); failure(error); } finally { setValidating(false); }
  };

  return <AppShell><Stack spacing={5}>
    <Box><Text color="primary.600" _dark={{ color: "primary.300" }} fontSize="xs" fontWeight="800" textTransform="uppercase" letterSpacing=".12em">Owner workspace</Text><Heading mt={1} size="lg">Settings</Heading><Text color="gray.600" _dark={{ color: "gray.400" }} mt={2}>Policy, access, continuity, identity, and advanced system controls.</Text></Box>
    <Grid templateColumns={{ base: "1fr", xl: "240px minmax(0, 1fr)" }} gap={5} alignItems="start">
      <Card as="nav" position={{ xl: "sticky" }} top={{ xl: 6 }} p={3} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="var(--radius-panel)"><Stack spacing={1}>{sectionNames.map((name) => <Button key={name} onClick={() => { const section = document.getElementById(name.toLowerCase().replace(/ /g, "-").replace("&", "and")); section?.scrollIntoView({ block: "start" }); section?.focus({ preventScroll: true }); }} variant="ghost" justifyContent="flex-start" size="sm">{name}</Button>)}</Stack></Card>
      <Stack spacing={5} minW={0}>
        {[["Access Groups", groupsQuery], ["Pricing", pricingQuery], ["Backup", backupQuery], ["Branding", brandingQuery]].map(([name, query]: any) => query.isLoading ? <Skeleton key={name} height="64px" aria-label={`Loading ${name}`} /> : query.isError ? <Alert key={name} status="error"><AlertIcon />{name} could not load.<Button ms={3} onClick={() => query.refetch()}>Retry</Button></Alert> : null)}
        <Section id="general" title="General" description="Shared defaults for the operator workspace."><Alert status="info" variant="left-accent"><AlertIcon />Locale, theme, and session behavior remain per operator.</Alert></Section>
        <Section id="users" title="Users" description="Creation paths and fixed duration choices."><Text fontSize="sm">User operations inherit the central backend policy. Manual duration choices use Owner presets below.</Text></Section>
        <Section id="admin-policies" title="Admin Policies" description="Delegation remains explicit and least-privileged."><HStack><Badge>Plan Only</Badge><Badge>Form Only</Badge><Badge>Both</Badge></HStack></Section>
        <Section id="plans-and-pricing" title="Plans & Pricing" description="Set Form pricing and duration presets. Commercial Plans are managed on the Owner Plans page.">
          {pricing && <Stack as="form" onSubmit={(event: FormEvent) => { event.preventDefault(); savePricing.mutate(); }} spacing={4}>
            <FormControl><FormLabel>Price per GiB (Toman)</FormLabel><Input type="number" min={0} value={pricing.price_per_gib_toman} onChange={(event) => setPricing({ ...pricing, price_per_gib_toman: Number(event.target.value) })} /></FormControl>
            <SimpleGrid columns={{ base: 1, md: 2 }} gap={3}>{pricing.duration_presets.map((preset, index) => <HStack key={preset.duration_days} p={3} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px"><Text flex="1">{preset.duration_days} days</Text><Input aria-label={`${preset.duration_days} day multiplier`} type="number" step="0.0001" min="0.0001" w="110px" value={preset.multiplier} onChange={(event) => { const duration_presets = [...pricing.duration_presets]; duration_presets[index] = { ...preset, multiplier: Number(event.target.value) }; setPricing({ ...pricing, duration_presets }); }} /><Switch aria-label={`Enable ${preset.duration_days} days`} isChecked={preset.enabled} onChange={(event) => { const duration_presets = [...pricing.duration_presets]; duration_presets[index] = { ...preset, enabled: event.target.checked }; setPricing({ ...pricing, duration_presets }); }} /></HStack>)}</SimpleGrid>
            <Button type="submit" alignSelf="flex-start" colorScheme="primary" isLoading={savePricing.isLoading}>Save pricing</Button>
          </Stack>}
        </Section>
        <Section id="access-groups" title="Access Groups" description="Network assignments are independent from commercial Plans."><Text fontSize="sm">Group management uses the Owner API; users can select a group when creating from a Plan. Network assignments remain independent from pricing.</Text><Stack mt={3}>{groupsQuery.data?.map((group) => <Box key={group.id} p={3} borderWidth="1px" borderRadius="md"><Text fontWeight="700">{group.name} {group.archived_at && <Badge>Archived</Badge>}</Text><Text fontSize="sm">{group.active_user_count} active users · {group.inbounds.join(", ")}</Text></Box>)}{groupsQuery.isSuccess && !groupsQuery.data?.length && <Text role="status">No Access Groups configured.</Text>}</Stack></Section>
        <Section id="nodes" title="Nodes" description="Health, collector telemetry, and reconnect state."><Text fontSize="sm">Node operations remain in the sidebar quick controls. Telemetry loss is reported separately from client inactivity.</Text></Section>
        <Section id="backup-and-restore" title="Backup & Restore" description="Logical MySQL backups, archive validation, and offline recovery.">
          {backup && <Stack spacing={4}>
            <HStack justify="space-between"><Box><Text fontWeight="700">Scheduled backups</Text><Text color="gray.600" _dark={{ color: "gray.400" }} fontSize="sm">Keep a recoverable local copy even when delivery fails.</Text></Box><Switch aria-label="Scheduled backups" isChecked={backup.enabled} onChange={(event) => setBackup({ ...backup, enabled: event.target.checked })} /></HStack>
            <SimpleGrid columns={{ base: 1, md: 3 }} gap={3}><FormControl><FormLabel>Destination</FormLabel><Select value={backup.destination} onChange={(event) => setBackup({ ...backup, destination: event.target.value as BackupSettings["destination"] })}><option value="LOCAL">Local</option><option value="TELEGRAM">Telegram</option><option value="EMAIL">Email</option><option value="TELEGRAM_EMAIL">Telegram + Email</option></Select></FormControl><FormControl><FormLabel>Schedule</FormLabel><Select value={backup.schedule} onChange={(event) => setBackup({ ...backup, schedule: event.target.value })}>{["15m","30m","1h","3h","6h","12h","24h"].map((value) => <option key={value}>{value}</option>)}</Select></FormControl><FormControl><FormLabel>Retention count</FormLabel><Input type="number" min={1} max={365} value={backup.retention_count} onChange={(event) => setBackup({ ...backup, retention_count: Number(event.target.value) })} /></FormControl></SimpleGrid>
            {backup.destination.includes("TELEGRAM") && <SimpleGrid columns={{ base: 1, md: 2 }} gap={3}><FormControl><FormLabel>Telegram bot token</FormLabel><Input type="password" placeholder={backup.telegram_configured ? "Configured · leave blank to keep" : "Required"} onChange={(event) => setBackup({ ...backup, telegram_bot_token: event.target.value || null })} /></FormControl><FormControl><FormLabel>Chat ID</FormLabel><Input value={backup.telegram_chat_id || ""} onChange={(event) => setBackup({ ...backup, telegram_chat_id: event.target.value })} /></FormControl></SimpleGrid>}
            {backup.destination.includes("EMAIL") && <SimpleGrid columns={{ base: 1, md: 2 }} gap={3}><FormControl><FormLabel>SMTP username</FormLabel><Input autoComplete="off" value={backup.smtp_username || ""} onChange={(event) => setBackup({ ...backup, smtp_username: event.target.value })} /></FormControl><FormControl><FormLabel>SMTP password</FormLabel><Input type="password" autoComplete="new-password" placeholder={backup.smtp_configured ? "Configured · leave blank to keep" : "Optional"} onChange={(event) => setBackup({ ...backup, smtp_password: event.target.value || null })} /></FormControl><FormControl><FormLabel>SMTP TLS</FormLabel><Switch isChecked={backup.smtp_use_tls} onChange={(event) => setBackup({ ...backup, smtp_use_tls: event.target.checked })} /></FormControl><FormControl><FormLabel>SMTP host</FormLabel><Input value={backup.smtp_host || ""} onChange={(event) => setBackup({ ...backup, smtp_host: event.target.value })} /></FormControl><FormControl><FormLabel>SMTP port</FormLabel><Input type="number" value={backup.smtp_port || ""} onChange={(event) => setBackup({ ...backup, smtp_port: Number(event.target.value) })} /></FormControl><FormControl><FormLabel>From</FormLabel><Input value={backup.email_from || ""} onChange={(event) => setBackup({ ...backup, email_from: event.target.value })} /></FormControl><FormControl><FormLabel>To</FormLabel><Input value={backup.email_to || ""} onChange={(event) => setBackup({ ...backup, email_to: event.target.value })} /></FormControl></SimpleGrid>}
            <HStack wrap="wrap"><Button colorScheme="primary" onClick={() => saveBackup.mutate()} isLoading={saveBackup.isLoading}>Save backup settings</Button><Button variant="outline" onClick={() => createBackup.mutate()} isLoading={createBackup.isLoading}>Back up now</Button></HStack>
            <Box pt={4} borderTopWidth="1px" borderColor="var(--panel-border)"><Text fontWeight="700">Validate backup for offline recovery</Text><Alert mt={2} status="warning"><AlertIcon />Online restore is disabled to protect active data. Stop all writers and use the documented isolated offline recovery procedure. Validation does not restore data.</Alert><HStack mt={3} wrap="wrap"><FormControl><FormLabel>Upload Backup</FormLabel><Input aria-label="Upload Backup" p={1.5} type="file" multiple maxW="420px" isDisabled={validating} onChange={(event) => { setValidation(null); const files = Array.from(event.target.files || []); setRestoreFiles(files); void validateRestore(files); }} /></FormControl></HStack>{restoreFiles.length > 0 && <Text mt={2} role="status">{restoreFiles.length > 1 ? `Split backup • ${restoreFiles.length} parts` : "Complete backup"} • {(restoreFiles.reduce((sum, file) => sum + file.size, 0) / 1024 ** 2).toFixed(1)} MB{validating ? " • Validating…" : ""}</Text>}{validation && <Alert mt={3} status="success"><AlertIcon />Archive validation passed. Offline recovery is still required.</Alert>}</Box>
          </Stack>}
        </Section>
        <Section id="branding" title="Branding" description="Owner-controlled name, logo, favicon, login title, and description.">
          {branding && <Stack spacing={4}><SimpleGrid columns={{ base: 1, md: 2 }} gap={3}><FormControl><FormLabel>Panel name</FormLabel><Input value={branding.panel_name} onChange={(event) => setBranding({ ...branding, panel_name: event.target.value })} /></FormControl><FormControl><FormLabel>Login title</FormLabel><Input value={branding.login_title} onChange={(event) => setBranding({ ...branding, login_title: event.target.value })} /></FormControl></SimpleGrid><FormControl><FormLabel>Optional description</FormLabel><Textarea value={branding.description || ""} onChange={(event) => setBranding({ ...branding, description: event.target.value })} /></FormControl><HStack wrap="wrap"><FormControl><FormLabel>Upload logo</FormLabel><Input isDisabled={uploading} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => uploadBrand("logo", event)} /></FormControl><FormControl><FormLabel>Upload favicon</FormLabel><Input isDisabled={uploading} type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => uploadBrand("favicon", event)} /></FormControl><Button colorScheme="primary" onClick={() => saveBranding.mutate()} isDisabled={uploading} isLoading={saveBranding.isLoading}>Save branding</Button></HStack></Stack>}
        </Section>
        <Section id="system" title="System" description="Rare and high-impact controls."><Alert status="warning" variant="left-accent"><AlertIcon />Core configuration, host replacement, and usage reset remain explicit sidebar actions.</Alert></Section>
      </Stack>
    </Grid>
  </Stack></AppShell>;
};

export default Settings;
