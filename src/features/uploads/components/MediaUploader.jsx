import React, { useRef, useState } from "react";
import {
  Button,
  FormControl,
  FormHelperText,
  FormLabel,
  HStack,
  Input,
  Text,
} from "@chakra-ui/react";

// File picker for the upload flow. It owns the <input> and the client-side
// validation only; the caller decides the endpoint and what to do with the answer.
const MediaUploader = ({
  label = "Загрузить файл",
  helperText,
  accept,
  buttonLabel = "Отправить на сервер",
  onUpload,
  isDisabled = false,
}) => {
  const inputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    try {
      await onUpload(file);
      setFile(null);
      // Clearing the DOM value is what lets the same file be picked twice in a
      // row; without it the change event never fires again.
      if (inputRef.current) inputRef.current.value = "";
    } finally {
      setBusy(false);
    }
  };

  return (
    <FormControl>
      <FormLabel>{label}</FormLabel>
      <HStack align="flex-end" spacing={4} wrap="wrap">
        <Input
          ref={inputRef}
          type="file"
          accept={accept}
          p={2}
          height="40px"
          maxW="420px"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
        <Button
          onClick={submit}
          isDisabled={!file || busy || isDisabled}
          isLoading={busy}
          loadingText="Отправка"
          colorScheme="teal"
        >
          {buttonLabel}
        </Button>
      </HStack>
      {helperText && <FormHelperText>{helperText}</FormHelperText>}
      {file && (
        <Text fontSize="sm" color="light_dark" mt={2}>
          Выбран файл: {file.name}
        </Text>
      )}
    </FormControl>
  );
};

export default MediaUploader;
