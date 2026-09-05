import {
  Alert, AlertDescription, Box, Button, Checkbox, FormControl, FormLabel,
  HStack, Input, Modal, ModalBody, ModalCloseButton, ModalContent, ModalFooter,
  ModalHeader, ModalOverlay, Select, SimpleGrid, Stack, Text, useToast,
} from "@chakra-ui/react";
import { FC, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetch } from "service/http";
import { BulkSelectionPreview, BulkSelectionResponse, BulkUserOperation, User } from "types/User";
import { localizedApiError } from "utils/apiError";

type InitialAction = { operation: BulkUserOperation; labelKey: string; kind: string; destructive?: boolean };
type Action = { operation: BulkUserOperation; amount?: number };
type Props = { users: User[]; action: InitialAction | null; isOpen: boolean; onClose: () => void; onSuccess: () => void };

const operations: Array<{ value: BulkUserOperation; label: string; amount: "none" | "traffic" | "days" }> = [
  { value: "activate", label: "Activate", amount: "none" },
  { value: "deactivate", label: "Deactivate", amount: "none" },
  { value: "add_data", label: "Add traffic", amount: "traffic" },
  { value: "subtract_data", label: "Subtract traffic", amount: "traffic" },
  { value: "add_days", label: "Add days", amount: "days" },
  { value: "subtract_days", label: "Subtract days", amount: "days" },
  { value: "delete", label: "Delete", amount: "none" },
];
const units = { MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4 } as const;

const compatible = (selected: BulkUserOperation[], candidate: BulkUserOperation) => {
  const next = new Set([...selected, candidate]);
  if (next.has("delete") && next.size > 1) return false;
  if (next.has("activate") && next.has("deactivate")) return false;
  if (next.has("add_data") && next.has("subtract_data")) return false;
  if (next.has("add_days") && next.has("subtract_days")) return false;
  return true;
};

export const CheckedBulkDialog: FC<Props> = ({ users, action, isOpen, onClose, onSuccess }) => {
  const { i18n } = useTranslation();
  const toast = useToast();
  const [selected, setSelected] = useState<BulkUserOperation[]>([]);
  const [traffic, setTraffic] = useState("1");
  const [days, setDays] = useState("30");
  const [unit, setUnit] = useState<keyof typeof units>("GB");
  const [preview, setPreview] = useState<BulkSelectionPreview | null>(null);
  const [result, setResult] = useState<BulkSelectionResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [previewKey, setPreviewKey] = useState("");
  const execution = useRef<{ key: string; id: string } | null>(null);

  useEffect(() => {
    if (!isOpen || !action) return;
    setSelected(action.operation === "add_data_and_days" ? ["add_data", "add_days"] : [action.operation]);
    setTraffic("1"); setDays("30"); setPreview(null); setResult(null);
    execution.current = null;
  }, [isOpen, action]);

  const actions = useMemo<Action[]>(() => selected.map((operation) => {
    const definition = operations.find((item) => item.value === operation)!;
    if (definition.amount === "traffic") return { operation, amount: Math.round(Number(traffic) * units[unit]) };
    if (definition.amount === "days") return { operation, amount: Math.round(Number(days)) };
    return { operation };
  }), [selected, traffic, days, unit]);
  const valid = users.length > 0 && actions.length > 0 && actions.every((item) => item.amount === undefined || Number.isInteger(item.amount) && item.amount > 0);
  const payloadKey = JSON.stringify({ user_ids: users.map((user) => user.id), actions });

  useEffect(() => {
    setPreview(null);
    setPreviewKey("");
    if (!isOpen || !valid) { setPreview(null); return; }
    const controller = new AbortController();
    const timer = window.setTimeout(() => fetch<BulkSelectionPreview>("/users/bulk-selection/preview", {
      method: "POST",
      body: { operation_id: `bulk-preview-${crypto.randomUUID()}`, user_ids: users.map((user) => user.id), actions },
      signal: controller.signal,
    }).then((value) => {
      if (!controller.signal.aborted) { setPreview(value); setPreviewKey(payloadKey); }
    }).catch((error) => {
      if (!controller.signal.aborted) toast({ title: "Preview failed", description: localizedApiError(error), status: "error" });
    }), 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [isOpen, valid, payloadKey, toast]);

  const execute = async () => {
    if (!valid || !preview || previewKey !== payloadKey || busy || result) return;
    if (execution.current?.key !== payloadKey) execution.current = { key: payloadKey, id: `bulk-user-${crypto.randomUUID()}` };
    setBusy(true);
    try {
      const response = await fetch<BulkSelectionResponse>("/users/bulk-selection/execute", {
        method: "POST",
        body: { operation_id: execution.current.id, user_ids: users.map((user) => user.id), actions },
      });
      setResult(response);
      toast({ title: `${response.success} succeeded`, description: response.failed ? `${response.failed} failed` : undefined, status: response.failed ? "warning" : "success" });
      onSuccess();
    } catch (error) {
      toast({ title: "Bulk operation failed", description: localizedApiError(error), status: "error" });
    } finally { setBusy(false); }
  };

  return <Modal isOpen={isOpen} onClose={busy ? () => undefined : onClose} isCentered size="xl">
    <ModalOverlay bg="blackAlpha.700" backdropFilter="blur(6px)" />
    <ModalContent dir={i18n.dir()} mx={3}>
      <ModalHeader>Bulk actions · {users.length} selected</ModalHeader><ModalCloseButton isDisabled={busy} />
      <ModalBody><Stack as="fieldset" disabled={busy || !!execution.current} minW={0} spacing={5}>
        <SimpleGrid columns={{ base: 1, sm: 2 }} gap={2}>
          {operations.map((item) => <Checkbox key={item.value} minH="44px" p={2} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px"
            isChecked={selected.includes(item.value)}
            isDisabled={!selected.includes(item.value) && !compatible(selected, item.value)}
            onChange={(event) => setSelected((current) => event.target.checked ? [...current, item.value] : current.filter((value) => value !== item.value))}>
            {item.label}
          </Checkbox>)}
        </SimpleGrid>
        {selected.some((item) => item === "add_data" || item === "subtract_data") && <FormControl><FormLabel>Traffic amount</FormLabel><HStack dir="ltr"><Input type="number" min="0.01" step="0.25" value={traffic} onChange={(event) => setTraffic(event.target.value)} /><Select w="110px" value={unit} onChange={(event) => setUnit(event.target.value as keyof typeof units)}><option>MB</option><option>GB</option><option>TB</option></Select></HStack></FormControl>}
        {selected.some((item) => item === "add_days" || item === "subtract_days") && <FormControl><FormLabel>Duration preset (days)</FormLabel><Select value={days} onChange={(event) => setDays(event.target.value)}><option value="1">1</option><option value="7">7</option><option value="30">30</option><option value="60">60</option></Select></FormControl>}
        {preview && <Alert status={selected.includes("delete") ? "error" : "info"} role="status"><AlertDescription>
          {preview.user_count} users · Traffic {preview.traffic_change.toLocaleString()} bytes · Duration {preview.duration_change_days} days · Cost {preview.cost_toman.toLocaleString()} Toman
        </AlertDescription></Alert>}
        {result && <Box role="status" aria-live="polite" p={3} borderWidth="1px" borderColor="var(--panel-border)" borderRadius="10px"><Text fontWeight="700">Success {result.success} · Failed {result.failed}</Text><Stack mt={2} maxH="140px" overflowY="auto">{result.results.filter((item) => item.status === "FAILED").map((item) => <Text key={item.user_id} fontSize="xs" dir="ltr">{item.username}: {item.reason}</Text>)}</Stack></Box>}
      </Stack></ModalBody>
      <ModalFooter gap={2}><Button variant="ghost" onClick={onClose} isDisabled={busy}>{result ? "Close" : "Cancel"}</Button><Button colorScheme={selected.includes("delete") ? "red" : "primary"} onClick={execute} isLoading={busy} isDisabled={!valid || !preview || previewKey !== payloadKey || !!result}>Apply to checked users</Button></ModalFooter>
    </ModalContent>
  </Modal>;
};
