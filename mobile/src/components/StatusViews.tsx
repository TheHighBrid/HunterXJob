import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { PrimaryButton } from "@/components/PrimaryButton";
import { useTheme } from "@/theme";

export function LoadingView({ label }: { label?: string }) {
  const theme = useTheme();
  return (
    <View style={styles.center}>
      <ActivityIndicator color={theme.primary} size="large" />
      {label ? <Text style={[styles.label, { color: theme.textMuted }]}>{label}</Text> : null}
    </View>
  );
}

export function ErrorView({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  const theme = useTheme();
  return (
    <View style={styles.center}>
      <Text style={[styles.errorTitle, { color: theme.danger }]}>Something went wrong</Text>
      <Text style={[styles.errorMessage, { color: theme.textMuted }]}>{message}</Text>
      {onRetry ? (
        <View style={styles.retryButton}>
          <PrimaryButton title="Retry" onPress={onRetry} />
        </View>
      ) : null}
    </View>
  );
}

export function EmptyView({ message }: { message: string }) {
  const theme = useTheme();
  return (
    <View style={styles.center}>
      <Text style={[styles.errorMessage, { color: theme.textFaint }]}>{message}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 32,
    paddingVertical: 48,
  },
  label: {
    marginTop: 12,
    fontSize: 14,
  },
  errorTitle: {
    fontSize: 16,
    fontWeight: "700",
    marginBottom: 6,
  },
  errorMessage: {
    fontSize: 14,
    textAlign: "center",
    lineHeight: 20,
  },
  retryButton: {
    marginTop: 16,
  },
});
