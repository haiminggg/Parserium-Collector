import { Alert, Button, Card, PasswordInput, Stack, Text, Title } from "@mantine/core";
import { type FormEvent, useState } from "react";

interface PairingScreenProps {
  onPair: (code: string) => Promise<void>;
}

export function PairingScreen({ onPair }: PairingScreenProps) {
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [failed, setFailed] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = code.trim();
    if (!value) return;
    setSubmitting(true);
    setFailed(false);
    try {
      await onPair(value);
    } catch {
      setFailed(true);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card component="section" withBorder maw={520} mx="auto" mt="xl" p="xl">
      <form onSubmit={submit}>
        <Stack gap="md">
          <div>
            <Title order={1}>Pair this browser</Title>
            <Text mt="xs">
              Enter the temporary code printed in the local API container log.
            </Text>
          </div>
          {failed ? (
            <Alert role="alert" color="red" title="Pairing failed">
              The pairing code is invalid or expired.
            </Alert>
          ) : null}
          <PasswordInput
            label="Pairing code"
            value={code}
            onChange={(event) => setCode(event.currentTarget.value)}
            autoComplete="one-time-code"
            required
          />
          <Button type="submit" loading={submitting} disabled={!code.trim()}>
            Pair browser
          </Button>
        </Stack>
      </form>
    </Card>
  );
}
