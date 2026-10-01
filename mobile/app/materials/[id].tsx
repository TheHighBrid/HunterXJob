import { useLocalSearchParams, useRouter } from "expo-router";
import { useState } from "react";
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";

import { api, describeError } from "@/api/client";
import type { JobDetail, JobMaterials, Material, MaterialDetail } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { LockBanner } from "@/components/LockBanner";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { confirmAsync } from "@/lib/confirm";
import { byKind, kindLabel, MATERIAL_KINDS, materialsSummary, shortHash, statusTone, type MaterialKind, type StatusTone } from "@/materials";
import { stageColor, useTheme, type Theme } from "@/theme";
import { formatDateTime, humanize } from "@/utils/format";

type Refresh = () => Promise<void>;

function toneColor(theme: Theme, tone: StatusTone): string {
  switch (tone) {
    case "success":
      return theme.success;
    case "warning":
      return theme.warning;
    case "danger":
      return theme.danger;
    default:
      return theme.textMuted;
  }
}

async function loadScreen(id: string): Promise<{ job: JobDetail; materials: JobMaterials }> {
  const [job, materials] = await Promise.all([api.job(id), api.jobMaterials(id)]);
  return { job, materials };
}

function useMaterialActions(refresh: Refresh) {
  const [busy, setBusy] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();

  async function run(key: string, action: () => Promise<string>) {
    setBusy(key);
    setError(undefined);
    try {
      setMessage(await action());
      await refresh();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(undefined);
    }
  }

  async function decide(item: Material, decision: "approve" | "reject") {
    const label = `${kindLabel(item.kind)} v${item.version}`;
    const body = decision === "approve"
      ? "Approve this exact version? Only approved versions are ever attached, and nothing is submitted (live submission stays locked)."
      : "Reject this version? It will never be attached. You can regenerate a new draft.";
    if (!(await confirmAsync(`${decision === "approve" ? "Approve" : "Reject"} ${label}`, body, decision === "approve" ? "Approve" : "Reject", decision === "reject"))) return;
    await run(`${decision}:${item.id}`, async () => {
      const result = decision === "approve" ? await api.approveMaterial(item.id) : await api.rejectMaterial(item.id);
      return `${label} ${result.material.status}. Job is ${humanize(result.job_stage)}. Nothing was submitted.`;
    });
  }

  async function generate(jobId: string) {
    await run("generate", async () => {
      const result = await api.generateMaterials(jobId);
      return `Generated new drafts (${result.items.filter((item) => item.status === "draft").length} pending). Review them below.`;
    });
  }
  return { busy, message, error, decide, generate };
}

type Actions = ReturnType<typeof useMaterialActions>;

function StatusCard({ job, materials }: { job: JobDetail; materials: JobMaterials }) {
  const theme = useTheme();
  return (
    <Card title="Application materials" right={<Badge label={humanize(materials.job_stage)} color={stageColor(theme, materials.job_stage)} />}>
      <Text style={[styles.title, { color: theme.text }]}>{job.title}</Text>
      <Muted>{job.company} · {job.location ?? "—"}</Muted>
      <Muted>{materialsSummary(materials.items)}</Muted>
      <Row label="LLM rewording" value={materials.llm_enabled ? "On (guarded)" : "Off (template)"} />
      {materials.generate_blockers.map((blocker) => (
        <Banner key={blocker} tone="warning" message={blocker} />
      ))}
    </Card>
  );
}

function statusWord(value: unknown, fallback: string): string {
  return typeof value === "string" ? humanize(value).toLowerCase() : fallback;
}

function PreviewText({ id }: { id: string }) {
  const theme = useTheme();
  const { data, loading, error } = useApiResource<MaterialDetail>(() => api.material(id), [id]);
  if (loading) return <Muted>Loading preview…</Muted>;
  if (!data) return <Banner tone="danger" message={error ?? "Preview unavailable."} />;
  return (
    <View style={{ gap: 6 }}>
      <View style={[styles.preview, { borderColor: theme.border, backgroundColor: theme.surfaceAlt }]}>
        <Text style={[styles.previewText, { color: theme.text }]}>{data.text}</Text>
      </View>
      <Muted>Built from {data.facts_used.length} verified facts · truthfulness guard {statusWord(data.guard.status, "unknown")} · LLM {statusWord(data.llm_report.status, "disabled")}</Muted>
    </View>
  );
}

function MaterialCard({ item, older, actions }: { item: Material; older: Material[]; actions: Actions }) {
  const theme = useTheme();
  const [showPreview, setShowPreview] = useState(item.status === "draft");
  const isDraft = item.status === "draft";
  return (
    <Card title={`${kindLabel(item.kind)} · v${item.version}`} right={<Badge label={item.status} color={toneColor(theme, statusTone(item.status))} />}>
      <Row label="Generator" value={`${item.generator}${item.llm === "accepted" ? " + LLM" : ""}`} />
      <Row label="Created" value={formatDateTime(item.created_at)} />
      <Row label="Content hash" value={shortHash(item.content_sha256)} mono />
      <Row label="PDF hash" value={shortHash(item.pdf_sha256)} mono />
      {item.decision_note ? <Muted>Note: {item.decision_note}</Muted> : null}
      {showPreview ? <PreviewText id={item.id} /> : null}
      <PrimaryButton title={showPreview ? "Hide preview" : "Preview text"} variant="secondary" onPress={() => {
        setShowPreview(!showPreview);
      }} />
      {isDraft ? (
        <View style={styles.actions}>
          <PrimaryButton title="Approve" loading={actions.busy === `approve:${item.id}`} disabled={!!actions.busy} onPress={() => void actions.decide(item, "approve")} />
          <PrimaryButton title="Reject" variant="danger" loading={actions.busy === `reject:${item.id}`} disabled={!!actions.busy} onPress={() => void actions.decide(item, "reject")} />
        </View>
      ) : null}
      {older.length ? <Muted>Earlier: {older.map((old) => `v${old.version} ${old.status}`).join(", ")}</Muted> : null}
    </Card>
  );
}

function KindSection({ kind, items, actions }: { kind: MaterialKind; items: Material[]; actions: Actions }) {
  const { latest, older } = byKind(items, kind);
  if (!latest) return null;
  return <MaterialCard key={latest.id} item={latest} older={older} actions={actions} />;
}

function GenerateButtons({ jobId, materials, actions }: { jobId: string; materials: JobMaterials; actions: Actions }) {
  const router = useRouter();
  const hasItems = materials.items.length > 0;
  return (
    <>
      <PrimaryButton title={hasItems ? "Regenerate drafts" : "Generate drafts"} variant={hasItems ? "secondary" : "primary"} disabled={!materials.can_generate || !!actions.busy} loading={actions.busy === "generate"} onPress={() => void actions.generate(jobId)} />
      <PrimaryButton title="Profile (verified facts)" variant="secondary" onPress={() => {
        router.push("/profile");
      }} />
    </>
  );
}

export default function MaterialsScreen() {
  const theme = useTheme();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, loading, refreshing, error, refresh, reload } = useApiResource(() => loadScreen(String(id)), [id]);
  const actions = useMaterialActions(refresh);

  if (loading && !data) return <ScreenContainer><LoadingView /></ScreenContainer>;
  if (!data) return <ScreenContainer><ErrorView message={error ?? "Materials unavailable."} onRetry={reload} /></ScreenContainer>;
  const { job, materials } = data;

  return (
    <ScreenContainer>
      <ScrollView contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}>
        {error ? <Banner tone="danger" message={error} /> : null}
        <StatusCard job={job} materials={materials} />
        {actions.message ? <Banner tone="info" message={actions.message} /> : null}
        {actions.error ? <Banner tone="danger" message={actions.error} /> : null}
        {MATERIAL_KINDS.map((kind) => (
          <KindSection key={kind} kind={kind} items={materials.items} actions={actions} />
        ))}
        <GenerateButtons jobId={job.id} materials={materials} actions={actions} />
        <LockBanner />
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  title: { fontSize: 18, fontWeight: "800" },
  actions: { gap: 10 },
  preview: { borderWidth: 1, borderRadius: 10, padding: 10 },
  previewText: { fontSize: 12, lineHeight: 17, fontFamily: "monospace" },
});
