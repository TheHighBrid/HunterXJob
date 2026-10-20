import { useRouter } from "expo-router";
import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { testConnection, type ConnectionReport, type StepStatus } from "@/api/connectionTest";
import { Banner } from "@/components/Banner";
import { Card, Muted } from "@/components/Card";
import { PrimaryButton } from "@/components/PrimaryButton";
import { confirmAsync } from "@/lib/confirm";
import { SERVER_URL_PLACEHOLDER } from "@/lib/url";
import { useConnection } from "@/store/connection";
import { useTheme, type Theme } from "@/theme";

const STEP_ICON: Record<StepStatus, string> = { ok: "✅", warn: "⚠️", fail: "❌", skipped: "○" };

function stepColor(theme: Theme, status: StepStatus): string {
  return status === "ok" ? theme.success : status === "warn" ? theme.warning : status === "fail" ? theme.danger : theme.textFaint;
}

function useConnectionForm() {
  const router = useRouter();
  const saved = useConnection();
  const [url, setUrl] = useState(saved.baseUrl);
  const [key, setKey] = useState(saved.apiKey);
  const [testing, setTesting] = useState(false);
  const [report, setReport] = useState<ConnectionReport>();
  const [message, setMessage] = useState<string>();

  // The key is loaded from the secure store asynchronously; pick it up once it arrives.
  useEffect(() => {
    if (saved.ready) {
      setUrl((current) => current || saved.baseUrl);
      setKey((current) => current || saved.apiKey);
    }
  }, [saved.ready, saved.baseUrl, saved.apiKey]);

  async function runTest(): Promise<ConnectionReport> {
    setTesting(true);
    setMessage(undefined);
    try {
      const result = await testConnection(url, key);
      setReport(result);
      return result;
    } finally {
      setTesting(false);
    }
  }

  async function save() {
    const result = await runTest();
    if (!result.ok || !result.url) {
      setMessage("Not saved: fix the failing step first.");
      return;
    }
    await saved.setConnection(result.url, key.trim());
    setMessage("Saved. The API key is stored in this phone's secure storage.");
    if (router.canGoBack()) router.back();
  }

  async function forget() {
    if (!(await confirmAsync("Forget this server?", "Removes the saved URL and API key from this phone.", "Forget", true))) return;
    await saved.forget();
    setUrl("");
    setKey("");
    setReport(undefined);
    setMessage("Connection removed.");
  }

  return { savedUrl: saved.baseUrl, url, setUrl, key, setKey, testing, report, message, runTest, save, forget };
}

type ServerFormProps = { url: string; setUrl: Dispatch<SetStateAction<string>>; apiKey: string; setKey: Dispatch<SetStateAction<string>> };

function ServerForm({ url, setUrl, apiKey, setKey }: ServerFormProps) {
  const theme = useTheme();
  const [showKey, setShowKey] = useState(false);
  const inputStyle = [styles.input, { color: theme.text, backgroundColor: theme.surfaceAlt, borderColor: theme.border }];
  return (
    <Card title="HunterXJob server">
      <Text style={[styles.label, { color: theme.text }]}>Server URL</Text>
      <TextInput
        value={url}
        onChangeText={setUrl}
        placeholder={SERVER_URL_PLACEHOLDER}
        placeholderTextColor={theme.textFaint}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
        style={inputStyle}
        accessibilityLabel="Server URL"
      />
      <Muted>{"Your VM's Tailscale name or 100.x address and port 8011, or an https:// tunnel URL."}</Muted>

      <Text style={[styles.label, { color: theme.text }]}>API key</Text>
      <View style={styles.keyRow}>
        <TextInput
          value={apiKey}
          onChangeText={setKey}
          placeholder="API_KEY from the server's .env"
          placeholderTextColor={theme.textFaint}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry={!showKey}
          style={[inputStyle, { flex: 1 }]}
          accessibilityLabel="API key"
        />
        <PrimaryButton title={showKey ? "Hide" : "Show"} variant="secondary" onPress={() => {
          setShowKey((v) => !v);
        }} />
      </View>
      <Muted>{"On the server: grep API_KEY v2/.env. The key is kept in the phone's secure storage, never in plain app storage."}</Muted>
    </Card>
  );
}

function ReportCard({ report }: { report?: ConnectionReport }) {
  const theme = useTheme();
  if (!report) return null;
  return (
    <Card title="Connection test">
      {report.steps.map((step) => (
        <View key={step.id} style={styles.step}>
          <Text style={styles.stepIcon}>{STEP_ICON[step.status]}</Text>
          <View style={{ flex: 1 }}>
            <Text style={[styles.stepLabel, { color: stepColor(theme, step.status) }]}>{step.label}</Text>
            {step.detail ? <Text style={[styles.stepDetail, { color: theme.textMuted }]}>{step.detail}</Text> : null}
          </View>
        </View>
      ))}
    </Card>
  );
}

export default function ConnectionScreen() {
  const theme = useTheme();
  const c = useConnectionForm();
  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: theme.background }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <ServerForm url={c.url} setUrl={c.setUrl} apiKey={c.key} setKey={c.setKey} />
        <View style={styles.buttons}>
          <View style={{ flex: 1 }}>
            <PrimaryButton title="Test connection" variant="secondary" onPress={() => void c.runTest()} loading={c.testing} />
          </View>
          <View style={{ flex: 1 }}>
            <PrimaryButton title="Test & save" onPress={() => void c.save()} disabled={c.testing} />
          </View>
        </View>
        {c.message ? <Banner tone={c.message.startsWith("Not saved") ? "danger" : "info"} message={c.message} /> : null}
        <ReportCard report={c.report} />
        {c.savedUrl ? (
          <Card title="Saved connection">
            <Muted>{c.savedUrl}</Muted>
            <PrimaryButton title="Forget this server" variant="danger" onPress={() => void c.forget()} />
          </Card>
        ) : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  content: { padding: 16, gap: 14 },
  label: { fontSize: 14, fontWeight: "700" },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  keyRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  buttons: { flexDirection: "row", gap: 10 },
  step: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  stepIcon: { fontSize: 16, width: 22 },
  stepLabel: { fontSize: 14, fontWeight: "700" },
  stepDetail: { fontSize: 13, lineHeight: 18 },
});
