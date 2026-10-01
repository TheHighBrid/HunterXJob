import { useState } from "react";
import { Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { useRouter } from "expo-router";

import type { JobDetail } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { PrimaryButton } from "@/components/PrimaryButton";
import { livenessInfo, livenessLine, matchMethodLabel, sourceLabel, type Tone } from "@/discovery";
import { stageColor, useTheme, type Theme } from "@/theme";
import { formatDateTime, humanize } from "@/utils/format";

function toneColor(theme: Theme, tone: Tone): string {
  switch (tone) {
    case "success":
      return theme.success;
    case "warning":
      return theme.warning;
    case "danger":
      return theme.danger;
    case "info":
      return theme.info;
    default:
      return theme.textMuted;
  }
}

export function LivenessBadge({ status }: { status: string | null | undefined }) {
  const theme = useTheme();
  const info = livenessInfo(status);
  if (!info) return null;
  return <Badge label={info.label} color={toneColor(theme, info.tone)} />;
}

type Action = () => Promise<void>;

export function LivenessCard({ job, onCheck }: { job: JobDetail; onCheck: Action }) {
  const theme = useTheme();
  const [busy, setBusy] = useState(false);
  const detail = job.liveness_detail;
  const info = livenessInfo(detail.status);
  async function check() {
    setBusy(true);
    try {
      await onCheck();
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Posting status" right={info ? <Badge label={info.label} color={toneColor(theme, info.tone)} /> : null}>
      <Muted>{info ? info.explanation : "Not checked yet. Queued postings are checked before they are prepared or dry-run."}</Muted>
      <Row label="Source" value={sourceLabel(job.source)} />
      {job.canonical_id ? <Row label="Posting ID" value={job.canonical_id} mono /> : null}
      {detail.checked_at ? <Row label="Last confirmed" value={formatDateTime(detail.checked_at)} /> : null}
      {detail.next_check_at ? <Row label="Next check" value={formatDateTime(detail.next_check_at)} /> : null}
      {detail.failures ? <Row label="Inconclusive checks" value={String(detail.failures)} valueColor={theme.warning} /> : null}
      {detail.closed_at ? <Row label="Closed" value={formatDateTime(detail.closed_at)} valueColor={theme.danger} /> : null}
      {detail.last_seen_at ? <Row label="Last seen on board" value={formatDateTime(detail.last_seen_at)} /> : null}
      {detail.checks.map((item) => (
        <Text key={`${item.checked_at ?? ""}-${item.trigger}-${item.signal}`} style={[styles.small, { color: theme.textMuted }]}>
          {formatDateTime(item.checked_at)} · {livenessLine(item)}
        </Text>
      ))}
      <PrimaryButton title="Check posting now (read-only)" variant="secondary" loading={busy} onPress={() => void check()} />
    </Card>
  );
}

export function LinkedPostingsCard({ job, onUnlink }: { job: JobDetail; onUnlink: Action }) {
  const theme = useTheme();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  if (!job.linked_postings.length) return null;
  async function unlink() {
    setBusy(true);
    setError(undefined);
    try {
      await onUnlink();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title={job.duplicate_of_id ? "Duplicate of" : `Same role elsewhere (${job.linked_postings.length})`}>
      <Muted>
        {job.duplicate_of_id
          ? "This posting is the same role as the one below, so it is never prepared or dry-run on its own."
          : "These postings are the same role; only this one is prepared and dry-run."}
      </Muted>
      {job.linked_postings.map((posting) => (
        <Pressable
          key={posting.id}
          accessibilityRole="button"
          onPress={() => {
            router.push({ pathname: "/jobs/[id]", params: { id: posting.id } });
          }}
          style={({ pressed }) => [styles.posting, { borderColor: theme.border, opacity: pressed ? 0.7 : 1 }]}
        >
          <Text style={[styles.postingTitle, { color: theme.text }]}>{posting.title}</Text>
          <Text style={[styles.small, { color: theme.textMuted }]}>
            {sourceLabel(posting.source)} · {posting.location || "—"} · {matchMethodLabel(posting.method)}
          </Text>
          <View style={styles.badges}>
            <Badge label={posting.relation} color={theme.info} />
            <Badge label={humanize(posting.stage)} color={stageColor(theme, posting.stage)} />
            <Text style={[styles.link, { color: theme.primary }]} onPress={() => void Linking.openURL(posting.url)}>Open ›</Text>
          </View>
        </Pressable>
      ))}
      {error ? <Banner tone="danger" message={error} /> : null}
      {job.duplicate_of_id ? (
        <PrimaryButton title="Not a duplicate — re-check this posting" variant="secondary" loading={busy} onPress={() => void unlink()} />
      ) : null}
    </Card>
  );
}

const styles = StyleSheet.create({
  small: { fontSize: 12, lineHeight: 17 },
  posting: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 8, gap: 3 },
  postingTitle: { fontSize: 14, fontWeight: "600" },
  badges: { flexDirection: "row", gap: 6, alignItems: "center", flexWrap: "wrap" },
  link: { fontSize: 13, fontWeight: "700" },
});
