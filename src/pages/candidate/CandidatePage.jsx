import React, { useState } from "react";
import { Divider, Heading, Text, VStack, useToast } from "@chakra-ui/react";
import MediaUploader from "shared/components/media/MediaUploader";
import TableMatchedVacancies from "shared/components/tables/TableMatchedVacancies";
import CandidateService from "shared/api/services/candidateService";
import useCurrentUser from "shared/hooks/useCurrentUser";

// The candidate screen. One action exists in the backend for a candidate:
// POST /api/upload_video/inference/{user_id}, which stores the video, reads the
// candidate's profile out of it and fills the match table. Everything on this
// page either feeds that call or reads its result.
const CandidatePage = () => {
  const toast = useToast();
  const { user } = useCurrentUser();
  const [historyKey, setHistoryKey] = useState(0);

  const upload = async (file) => {
    if (!user?.userId) {
      toast({ title: "Нужен вход", status: "error", duration: 4000 });
      return;
    }

    try {
      await CandidateService.uploadVideo(user.userId, file);
      toast({
        title: "Видео загружено",
        description: "Обработка занимает время — подборка обновится ниже.",
        status: "success",
        duration: 5000,
      });
      // Bumping the key remounts the table, which is how the page asks the
      // recommendation endpoint for a fresh answer without duplicating the fetch
      // logic here.
      setHistoryKey((value) => value + 1);
    } catch (error) {
      toast({
        title: "Не удалось загрузить видео",
        description:
          (error && error.response && error.response.data && error.response.data.detail) ||
          (error && error.message) ||
          "Неизвестная ошибка",
        status: "error",
        duration: 6000,
      });
    }
  };

  return (
    <VStack
      padding={25}
      alignItems="flex-start"
      spacing="24px"
      flexGrow={1}
      width="100%"
    >
      <Heading as="h1" fontSize="22px" fontWeight={700}>
        Подбор вакансий
      </Heading>

      <Text fontSize={14} maxW="720px">
        Загрузите короткое видео о себе. Сервер извлечёт из него профиль и
        подберёт вакансии, которые подходят вам лучше всего.
      </Text>

      <MediaUploader
        label="Видео о себе"
        accept="video/*"
        helperText="Поддерживаются форматы mp4, mov, avi, webm. Обработка может занять несколько минут."
        buttonLabel="Загрузить видео"
        onUpload={upload}
        isDisabled={!user?.userId}
      />

      <Divider />

      <Heading as="h2" fontSize="18px" fontWeight={700}>
        Подходящие вакансии
      </Heading>
      {/* A key rather than an effect: the table owns its own fetch, and remounting
          is the smallest way to refresh it after an upload. */}
      <TableMatchedVacancies key={historyKey} userId={user?.userId} />
    </VStack>
  );
};

export default CandidatePage;
