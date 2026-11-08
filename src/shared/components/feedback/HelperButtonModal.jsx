import React from "react";
import {
  Accordion,
  AccordionButton,
  AccordionIcon,
  AccordionItem,
  AccordionPanel,
  Box,
  Button,
  Modal,
  ModalBody,
  ModalContent,
  ModalHeader,
  ModalOverlay,
  useDisclosure,
} from "@chakra-ui/react";
import { QuestionOutlineIcon } from "@chakra-ui/icons";

// The floating "?" in the corner. The old content was a warehouse manual (roles,
// accepting materials, writing off stock); these entries describe what this app
// actually does.
const FAQ = [
  {
    title: "Как загрузить видео?",
    body: "На главной странице выберите файл и отправьте его на сервер. Видео обрабатывается моделью, результат появляется в истории загрузок.",
  },
  {
    title: "Какие форматы поддерживаются?",
    body: "Видеофайлы, которые умеет читать ffmpeg. Документы принимаются в форматах PDF, DOC и DOCX.",
  },
  {
    title: "Почему обработка занимает время?",
    body: "Распознавание и построение эмбеддингов выполняются на видеокарте. Длинное видео обрабатывается дольше короткого.",
  },
  {
    title: "Где посмотреть историю?",
    body: "История загрузок доступна на главной странице. Пользователь видит только свои файлы, администратор — все.",
  },
];

const HelperButtonModal = () => {
  const { isOpen, onOpen, onClose } = useDisclosure();

  return (
    <>
      <Box position="fixed" right="10px" top="150px" zIndex={2}>
        <Button
          aria-label="Краткое руководство"
          bgColor="transparent"
          _hover={{ background: "transparent" }}
          onClick={onOpen}
        >
          <QuestionOutlineIcon boxSize={5} />
        </Button>
      </Box>

      <Modal size="sm" isOpen={isOpen} onClose={onClose} blockScrollOnMount={false}>
        <ModalOverlay />
        <ModalContent>
          <ModalHeader>FAQ (краткое руководство)</ModalHeader>
          <ModalBody pb={6}>
            <Accordion allowToggle>
              {FAQ.map((item) => (
                <AccordionItem key={item.title}>
                  <h2>
                    <AccordionButton>
                      <Box as="span" flex="1" textAlign="left">
                        {item.title}
                      </Box>
                      <AccordionIcon />
                    </AccordionButton>
                  </h2>
                  <AccordionPanel pb={4}>{item.body}</AccordionPanel>
                </AccordionItem>
              ))}
            </Accordion>
          </ModalBody>
        </ModalContent>
      </Modal>
    </>
  );
};

export default HelperButtonModal;
