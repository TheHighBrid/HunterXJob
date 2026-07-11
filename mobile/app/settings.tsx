import { useRouter } from "expo-router";
import { useEffect, useState } from "react";
import {
  Alert,
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import { api, describeError, isConnectivityError } from "@/api/client";
import { PrimaryButton } from "@/components/PrimaryButton";
import { ScreenContainer } from "@/components/ScreenContainer";
import { mockSettings } from "@/mock/data";
import { useSettingsStore } from "@/store/settings";
import { useTheme } from "@/theme";

export default function SettingsScreen() {
  const theme = useTheme();
  const router = useRouter();

  const storedBaseUrl = useSettingsStore((s) => s.baseUrl);
  const storedApiKey = useSettingsStore((s) => s.apiKey);
  const storedAutomationEnabled = useSettingsStore((s) => s.automationEnabled);
  const storedDailyCap = useSettingsStore((s) => s.dailyCap);
  const setConnection = useSettingsStore((s) => s.setConnection);
  const setAutomationPrefs = useSettingsStore((s) => s.setAutomationPrefs);
  const markSetupComplete = useSettingsStore((s) => s.markSetupComplete);

  const [baseUrl, setBaseUrl] = useState(storedBaseUrl);
  const [apiKey, setApiKey] = useState(storedApiKey);
  const [automationEnabled, setAutomationEnabled] = useState(storedAutomationEnabled);
  const [dailyCap, setDailyCap] = useState(String(storedDailyCap));
  const [showApiKey, setShowApiKey] = useState(false);

  const [llmInfo, setLlmInfo] = useState<{ provider: string; model: string } | null>(null);
  const [llmStatus, setLlmStatus] = useState<"idle" | "loading" | "error" | "demo">("idle");
  const [llmError, setLlmError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const fetchBackendSettings = async () => {
    if (!storedBaseUrl) return;
    setLlmStatus("loading");
    try {
      const remote = await api.getSettings();
      setLlmInfo({ provider: remote.llm_provider, model: remote.llm_model });
      setAutomationEnabled(remote.automation_enabled);
      setDailyCap(String(remote.max_applications_per_day));
      setLlmStatus("idle");
      setLlmError(null);
    } catch (err) {
      if (isConnectivityError(err)) {
        setLlmInfo({ provider: mockSettings.llm_provider, model: mockSettings.llm_model });
        setLlmStatus("demo");
      } else {
        setLlmStatus("error");
      }
      setLlmError(describeError(err));
    }
  };

  useEffect(() => {
    fetchBackendSettings();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSave = async () => {
    const trimmedUrl = baseUrl.trim().replace(/\/+$/, "");
    if (trimmedUrl && !/^https?:\/\//i.test(trimmedUrl)) {
      Alert.alert("Invalid URL", "Backend URL should start with http:// or https://");
      return;
    }
    const cap = Number(dailyCap);
    if (!Number.isFinite(cap) || cap < 0) {
      Alert.alert("Invalid daily cap", "Enter a whole number of 0 or more.");
      return;
    }

    setSaving(true);
    setConnection(trimmedUrl, apiKey.trim());
    setAutomationPrefs(automationEnabled, cap);
    markSetupComplete();

    let syncMessage = "Settings saved.";
    if (trimmedUrl) {
      try {
        const updated = await api.updateSettings({
          automation_enabled: automationEnabled,
          max_applications_per_day: cap,
        });
        setLlmInfo({ provider: updated.llm_provider, model: updated.llm_model });
        syncMessage = "Settings saved and synced with the backend.";
      } catch (err) {
        syncMessage = `Settings saved locally, but couldn't sync to the backend: ${describeError(err)}`;
      }
    } else {
      syncMessage = "Settings saved locally. Set a backend URL to sync automation settings.";
    }

    setSaving(false);
    Alert.alert("Saved", syncMessage, [
      {
        text: "OK",
        onPress: () => (router.canGoBack() ? router.back() : router.replace("/")),
      },
    ]);
  };

  const handleContinueWithDemoData = () => {
    markSetupComplete();
    router.canGoBack() ? router.back() : router.replace("/");
  };

  return (
    <ScreenContainer>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <Field label="Backend base URL" theme={theme}>
            <TextInput
              value={baseUrl}
              onChangeText={setBaseUrl}
              placeholder="https://your-server:8000"
              placeholderTextColor={theme.textFaint}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
              style={[styles.input, { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text }]}
            />
          </Field>

          <Field label="API key" theme={theme}>
            <View style={styles.apiKeyRow}>
              <TextInput
                value={apiKey}
                onChangeText={setApiKey}
                placeholder="X-API-Key value"
                placeholderTextColor={theme.textFaint}
                autoCapitalize="none"
                autoCorrect={false}
                secureTextEntry={!showApiKey}
                style={[
                  styles.input,
                  styles.apiKeyInput,
                  { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text },
                ]}
              />
              <PrimaryButton
                title={showApiKey ? "Hide" : "Show"}
                variant="secondary"
                onPress={() => setShowApiKey((v) => !v)}
              />
            </View>
          </Field>

          <Field label="Automation enabled" theme={theme}>
            <View style={styles.switchRow}>
              <Text style={{ color: theme.textMuted, fontSize: 13, flex: 1 }}>
                Let the backend automatically apply to matching jobs.
              </Text>
              <Switch
                value={automationEnabled}
                onValueChange={setAutomationEnabled}
                trackColor={{ false: theme.border, true: theme.primary }}
              />
            </View>
          </Field>

          <Field label="Daily application cap" theme={theme}>
            <TextInput
              value={dailyCap}
              onChangeText={setDailyCap}
              placeholder="15"
              placeholderTextColor={theme.textFaint}
              keyboardType="number-pad"
              style={[styles.input, { backgroundColor: theme.surface, borderColor: theme.border, color: theme.text }]}
            />
          </Field>

          <Field label="LLM provider (reported by backend)" theme={theme}>
            {llmStatus === "loading" ? (
              <Text style={{ color: theme.textFaint, fontSize: 13 }}>Checking backend…</Text>
            ) : llmInfo ? (
              <View>
                <Text style={{ color: theme.text, fontSize: 14, fontWeight: "600" }}>
                  {llmInfo.provider} · {llmInfo.model}
                </Text>
                {llmStatus === "demo" ? (
                  <Text style={{ color: theme.warning, fontSize: 12, marginTop: 4 }}>
                    Offline / demo data — {llmError}
                  </Text>
                ) : null}
              </View>
            ) : (
              <Text style={{ color: theme.textFaint, fontSize: 13 }}>
                {llmStatus === "error"
                  ? `Couldn't read from backend: ${llmError}`
                  : "Set a backend URL and save to see the LLM provider."}
              </Text>
            )}
          </Field>

          <View style={styles.saveRow}>
            <PrimaryButton title="Save" onPress={handleSave} loading={saving} />
          </View>

          {!storedBaseUrl ? (
            <View style={styles.demoRow}>
              <PrimaryButton
                title="Continue with offline/demo data"
                variant="secondary"
                onPress={handleContinueWithDemoData}
              />
              <Text style={[styles.demoHint, { color: theme.textFaint }]}>
                Explore the app with sample data before your backend is running. Screens will clearly mark
                data as demo data until you set a backend URL here.
              </Text>
            </View>
          ) : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </ScreenContainer>
  );
}

function Field({ label, theme, children }: { label: string; theme: ReturnType<typeof useTheme>; children: React.ReactNode }) {
  return (
    <View style={styles.field}>
      <Text style={[styles.label, { color: theme.textMuted }]}>{label}</Text>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: 16,
    paddingBottom: 60,
  },
  field: {
    marginBottom: 18,
  },
  label: {
    fontSize: 13,
    fontWeight: "700",
    marginBottom: 6,
  },
  input: {
    borderWidth: 1,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
  },
  apiKeyRow: {
    flexDirection: "row",
    gap: 8,
    alignItems: "center",
  },
  apiKeyInput: {
    flex: 1,
  },
  switchRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  saveRow: {
    marginTop: 8,
  },
  demoRow: {
    marginTop: 18,
    gap: 8,
  },
  demoHint: {
    fontSize: 12,
    lineHeight: 17,
  },
});
