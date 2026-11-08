import { useState } from "react";
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { api, describeError } from "@/api/client";
import { Badge } from "@/components/Badge";
import { Banner } from "@/components/Banner";
import { Card, Muted, Row } from "@/components/Card";
import { Chip } from "@/components/Chip";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { ErrorView, LoadingView } from "@/components/StatusViews";
import { useApiResource } from "@/hooks/useApiResource";
import { notify } from "@/lib/confirm";
import { useTheme } from "@/theme";
import { humanize } from "@/utils/format";

const SIMULATOR_PRESETS = [
  {
    label: "📍 Work Location",
    question: "Which work location would suit you?",
    options: "Toronto Office, Vancouver Hybrid, Remote - Canada, US Remote",
  },
  {
    label: "💰 Salary Range",
    question: "What are your compensation expectations?",
    options: "$45,000 - $55,000, $60,000 - $75,000 CAD, $80,000 - $95,000 CAD",
  },
  {
    label: "🕒 Time Zone",
    question: "What time zone are you located in?",
    options: "(GMT-08:00) Pacific Time, (GMT-05:00) Eastern Time (US & Canada), (GMT+00:00) UTC",
  },
  {
    label: "✅ Consent / Privacy",
    question: "Privacy Notice and Consent: Do you consent to the processing of your application data?",
    options: "Yes, I consent, No",
  },
];

async function loadVaultScreen() {
  const [answers, catalog] = await Promise.all([api.listAnswers(), api.vaultCatalog()]);
  return { answers, catalog };
}

export default function VaultScreen() {
  const theme = useTheme();
  const { data, loading, refreshing, error, refresh, reload } = useApiResource(loadVaultScreen, []);

  // Simulator state
  const [simQuestion, setSimQuestion] = useState(SIMULATOR_PRESETS[0].question);
  const [simOptions, setSimOptions] = useState(SIMULATOR_PRESETS[0].options);
  const [simRunning, setSimRunning] = useState(false);
  const [simResult, setSimResult] = useState<any | null>(null);

  // Add/update policy state
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [revalidating, setRevalidating] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  async function handleSimulate() {
    if (!simQuestion.trim()) return;
    setSimRunning(true);
    try {
      const opts = simOptions
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await api.simulateVault(simQuestion.trim(), opts.length > 0 ? opts : undefined);
      setSimResult(res);
    } catch (err) {
      notify("Simulation Error", describeError(err));
    } finally {
      setSimRunning(false);
    }
  }

  async function handleSavePolicy() {
    if (!newKey.trim() || !newValue.trim()) {
      notify("Missing Fields", "Enter both a question key/phrase and an answer value.");
      return;
    }
    setSaving(true);
    try {
      await api.saveAnswer({
        key: newKey.trim().toLowerCase().replace(/\s+/g, "_"),
        value: newValue.trim(),
        scope: "global",
        source: "user",
        note: `Added from mobile Answer Vault (${newKey.trim()})`,
      });
      setNewKey("");
      setNewValue("");
      notify("Saved to Vault ✓", "Answer stored and ready for automatic resolution.");
      await refresh();
    } catch (err) {
      notify("Save Failed", describeError(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleDeletePolicy(id: string) {
    try {
      await api.deleteAnswer(id);
      await refresh();
    } catch (err) {
      notify("Delete Failed", describeError(err));
    }
  }

  async function handleRevalidateAll() {
    setRevalidating(true);
    try {
      const res = await api.revalidateReviews();
      const resolved = res.resolved ?? res.revalidated ?? 0;
      const retained = res.retained ?? res.remaining_open ?? 0;
      notify("Reviews Revalidated", `Auto-resolved ${resolved} task(s). Remaining open: ${retained}.`);
      await refresh();
    } catch (err) {
      notify("Revalidate Error", describeError(err));
    } finally {
      setRevalidating(false);
    }
  }

  if (loading && !data) return <ScreenContainer><LoadingView label="Loading Answer Vault…" /></ScreenContainer>;
  if (!data) return <ScreenContainer><ErrorView message={error ?? "Could not load Vault."} onRetry={reload} /></ScreenContainer>;

  const filteredAnswers = data.answers.filter((a) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return a.key.toLowerCase().includes(q) || a.value.toLowerCase().includes(q) || (a.note || "").toLowerCase().includes(q);
  });

  return (
    <ScreenContainer>
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={theme.primary} />}
      >
        {error ? <Banner tone="danger" message={error} /> : null}

        <Card
          title="Smart Preference & Option Engine"
          accentColor={theme.primary}
          right={<Badge label={`${data.catalog.catalog_size} Catalog Rules`} color={theme.primary} />}
        >
          <Muted>
            Automatically resolves preferences, maps dropdown options semantically, and approves standard consent disclaimers before creating manual review tasks.
          </Muted>
          <Row label="Work Location" value="Ottawa, ON → Remote Canada" valueColor={theme.success} />
          <Row label="Work Arrangement" value="Remote / Hybrid Preferred" />
          <Row label="Desired Salary Floor" value="CAD $65,000 (Range-Aware)" valueColor={theme.success} />
          <Row label="Time Zone" value="Eastern Time (GMT-05:00)" />
          <Row label="Standard Consents" value="Universal Auto-Yes" valueColor={theme.success} />
          <PrimaryButton
            title="⚡ Re-Evaluate All Open Review Tasks Now"
            variant="secondary"
            loading={revalidating}
            onPress={() => void handleRevalidateAll()}
          />
        </Card>

        <Card
          title="Live Question & Dropdown Simulator"
          accentColor={theme.info}
          right={<Badge label="Interactive" color={theme.info} />}
        >
          <Muted>Tap a preset or type any question and dropdown choices to test how HunterXJob resolves it.</Muted>
          <View style={styles.chipsRow}>
            {SIMULATOR_PRESETS.map((preset) => (
              <Chip
                key={preset.label}
                label={preset.label}
                selected={simQuestion === preset.question}
                onPress={() => {
                  setSimQuestion(preset.question);
                  setSimOptions(preset.options);
                }}
              />
            ))}
          </View>
          <TextInput
            value={simQuestion}
            onChangeText={setSimQuestion}
            placeholder="Question label (e.g., Which work location would suit you?)"
            placeholderTextColor={theme.textFaint}
            style={[styles.input, { color: theme.text, backgroundColor: theme.surfaceAlt, borderColor: theme.border }]}
          />
          <TextInput
            value={simOptions}
            onChangeText={setSimOptions}
            placeholder="Dropdown options (comma-separated, optional)"
            placeholderTextColor={theme.textFaint}
            style={[styles.input, { color: theme.text, backgroundColor: theme.surfaceAlt, borderColor: theme.border }]}
          />
          <PrimaryButton
            title="🧪 Simulate Answer Resolution"
            variant="primary"
            loading={simRunning}
            onPress={() => void handleSimulate()}
          />
          {simResult ? (
            <View style={[styles.simResultBox, { backgroundColor: theme.surfaceAlt, borderColor: simResult.resolved ? theme.success : theme.warning }]}>
              <Row
                label="Resolution Status"
                value={simResult.resolved ? "Resolved Automatically ✓" : "Escalates to Review"}
                valueColor={simResult.resolved ? theme.success : theme.warning}
              />
              <Row label="Selected Answer" value={String(simResult.value ?? "—")} valueColor={theme.text} />
              <Row label="Canonical Key" value={humanize(simResult.canonical_key || simResult.category)} />
              <Row label="Source" value={humanize(simResult.source)} />
              <Row label="Confidence" value={`${Math.round((simResult.confidence ?? 0) * 100)}%`} />
            </View>
          ) : null}
        </Card>

        <Card title="Add / Override Answer Vault Rule" accentColor={theme.success}>
          <TextInput
            value={newKey}
            onChangeText={setNewKey}
            placeholder="Question key or topic (e.g., work_location, desired_salary)"
            placeholderTextColor={theme.textFaint}
            style={[styles.input, { color: theme.text, backgroundColor: theme.surfaceAlt, borderColor: theme.border }]}
          />
          <TextInput
            value={newValue}
            onChangeText={setNewValue}
            placeholder="Preferred answer value (e.g., Remote Canada)"
            placeholderTextColor={theme.textFaint}
            style={[styles.input, { color: theme.text, backgroundColor: theme.surfaceAlt, borderColor: theme.border }]}
          />
          <PrimaryButton
            title="💾 Save Rule to Answer Vault"
            variant="success"
            loading={saving}
            onPress={() => void handleSavePolicy()}
          />
        </Card>

        <Card
          title={`Saved Vault Overrides (${filteredAnswers.length})`}
          right={<Badge label="Persistent" color={theme.textMuted} />}
        >
          <TextInput
            value={searchQuery}
            onChangeText={setSearchQuery}
            placeholder="Search saved vault answers…"
            placeholderTextColor={theme.textFaint}
            style={[styles.input, { color: theme.text, backgroundColor: theme.surfaceAlt, borderColor: theme.border }]}
          />
          {filteredAnswers.length === 0 ? (
            <Muted>No custom overrides match your filter. Built-in catalog rules are active.</Muted>
          ) : (
            filteredAnswers.slice(0, 40).map((item) => (
              <View key={item.id} style={[styles.policyRow, { borderColor: theme.border }]}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={[styles.policyKey, { color: theme.text }]}>{item.key}</Text>
                  <Text style={[styles.policyVal, { color: theme.primary }]}>{item.value}</Text>
                  {item.note ? <Muted>{item.note}</Muted> : null}
                </View>
                <Pressable
                  accessibilityRole="button"
                  onPress={() => void handleDeletePolicy(item.id)}
                  style={[styles.deleteBtn, { borderColor: theme.danger + "66" }]}
                >
                  <Text style={{ color: theme.danger, fontSize: 12, fontWeight: "700" }}>Delete</Text>
                </Pressable>
              </View>
            ))
          )}
        </Card>
      </ScrollView>
    </ScreenContainer>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  chipsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 14 },
  simResultBox: { borderWidth: 1, borderRadius: 12, padding: 12, gap: 8, marginTop: 4 },
  policyRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: 10,
  },
  policyKey: { fontSize: 13, fontWeight: "800" },
  policyVal: { fontSize: 14, fontWeight: "600" },
  deleteBtn: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 5 },
});
