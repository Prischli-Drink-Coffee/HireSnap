-- phpMyAdmin SQL Dump
-- version 5.2.0
-- https://www.phpmyadmin.net/
--
-- Хост: 127.0.0.1:3306
-- Время создания: Ноя 06 2024 г., 00:31
-- Версия сервера: 5.7.39
-- Версия PHP: 7.2.34

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- База данных: `HireSnap`
--

-- --------------------------------------------------------

--
-- Структура таблицы `candidate_vacancies`
--

CREATE TABLE IF NOT EXISTS `candidate_vacancies` (
  `id` int(11) NOT NULL,
  `user_id` int(11) NOT NULL,
  `vacancy_id` int(11) NOT NULL,
  `distance` decimal(6,6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Структура таблицы `embeddings`
--

CREATE TABLE IF NOT EXISTS `embeddings` (
  `id` int(11) NOT NULL,
  `url` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Структура таблицы `files`
--

CREATE TABLE IF NOT EXISTS `files` (
  `id` int(11) NOT NULL,
  `url` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Структура таблицы `info_candidates`
--

CREATE TABLE IF NOT EXISTS `info_candidates` (
  `id` int(11) NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `phone` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `gender` enum('male','female') COLLATE utf8mb4_unicode_ci NULL DEFAULT NULL,
  `date_birth` timestamp NULL DEFAULT NULL,
  `file_id` int(11) DEFAULT NULL,
  `embedding_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Структура таблицы `magics`
--

CREATE TABLE IF NOT EXISTS `magics` (
  `id` int(11) NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `candidate_explanation` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `vacancy_explanation` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `candidate_answer` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `vacancy_answer` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `file_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Структура таблицы `personalities`
--

CREATE TABLE IF NOT EXISTS `personalities` (
  `id` int(11) NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `embedding_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO `personalities` (`id`, `name`, `description`, `embedding_id`) VALUES
(1, 'O', 'Openness to experience describes a person as curious, open to new impressions, and eager to learn. Such individuals have vivid imaginations, adapt easily to changes, enjoy exploring new things, and value original ideas. They are characterized by flexible thinking, a passion for art, culture, and science, and a high sensitivity to new experiences. They often seek unconventional solutions and are not afraid to experiment, which gives them a broad outlook. People open to experience are generally tolerant of uncertainty and ready for self-expression in various forms. In the professional sphere, such employees can become a valuable source of innovation and idea generators. They easily adapt to changes, which makes them highly sought after in dynamic and developing industries where flexibility and unconventional thinking are required. Their inclination towards research and experimentation helps in finding original solutions to complex problems. People with a high level of openness to experience learn quickly and are interested in self-development, which enables them to easily acquire new skills and technologies. These employees are beneficial for businesses striving for innovation and can become drivers of change within an organization. However, they may become overly absorbed in experiments and pay less attention to details, which can sometimes hinder the adherence to strictly structured processes.', 1),
(2, 'C', 'Conscientiousness characterizes a person as organized, responsible, and goal-oriented. Such individuals are attentive to details, inclined towards planning, and strive to perform tasks with the highest quality. Conscientious individuals possess strong self-discipline, take their commitments seriously, and often show persistence in achieving their set goals. They are characterized by neatness, reliability, and resilience to stress, allowing them to cope with difficulties without deviating from their principles. People with a high level of conscientiousness typically follow rules and adhere to established standards, which makes them responsible and predictable. In the professional sphere, conscientious individuals stand out as valuable employees who work systematically and thoroughly. Their focus on high-quality task execution makes them ideal candidates for positions where attention to detail and adherence to standards are crucial, such as in finance, management, healthcare, and other professional fields. They often achieve success by completing work on time and following requirements, making them excellent team players and reliable performers. Conscientious people frequently take on leadership and organizational roles, as they can manage resources and effectively plan tasks. They seek stable and structured work environments where they can apply their strong organizational skills. However, their perfectionism and tendency for structure may slow down work, particularly in situations that require flexibility and quick adaptation.', 2),
(3, 'E', 'Extraversion describes a person as active, sociable, and energetic. Extraverts gain energy from interacting with others, display friendly and confident behavior, enjoy being the center of attention, and easily connect with new acquaintances. They are open to communication, often optimistic, decisive, and inclined to take initiative. Extraverts prefer to actively participate in group activities and quickly adapt to new social situations. In the professional environment, extraverts become drivers of teamwork and often inspire colleagues with their enthusiasm. They are effective in roles that require a high degree of interaction with people, such as in sales, marketing, management, and other communication-driven fields. Extraverts tend to take on leadership positions, as they can confidently represent the company''s interests, negotiate, and make decisions under uncertainty. They quickly adapt to changes and are not afraid to share ideas, making them valuable for companies focused on growth and dynamic development. Their social skills contribute to creating a positive atmosphere within the team and supporting team spirit, which enhances overall team effectiveness. However, their desire for interaction may distract them from task execution, and it can sometimes reduce their focus on details and individual projects.', 3),
(4, 'A', 'Agreeableness characterizes a person as sensitive, kind-hearted, and cooperative. People with high agreeableness are generally friendly, empathetic, and responsive. They possess compassion and a willingness to help others, value harmony, and strive to avoid conflict. Such individuals are open to understanding different perspectives and exhibit tolerance towards others. Agreeable people aim to establish trusting and warm relationships and often display altruism. In the workplace, agreeable individuals become reliable team players, as their focus on cooperation and flexibility helps them connect with colleagues and clients. They contribute to creating a supportive and harmonious atmosphere, often serving as a link within the team and assisting in conflict resolution. Agreeable employees are well-suited for roles where working with people is key, such as in HR, education, social work, and healthcare. Their willingness to support colleagues and offer help makes them valuable for team projects and client interactions. They aim to build long-term working relationships and focus on collective benefit, making them valuable assets to companies with a strong culture of teamwork and high ethical standards. However, they may sometimes avoid difficult conversations and give in to pressure, which can hinder them from standing up for their ideas and making tough decisions.', 4),
(5, 'N', 'Neuroticism reflects a person''s tendency toward emotional instability, anxiety, and sensitivity to stress. People with a high level of neuroticism are often prone to worry, may experience intense emotions such as fear, anger, or depression, and are more likely to perceive situations as threats. They can struggle with managing their emotions and may be sensitive to criticism and failure. Neuroticism can manifest in tendencies toward self-criticism, mood swings, and withdrawal in stressful situations. In the professional environment, individuals with high levels of neuroticism may be cautious and carefully consider risks, which can be beneficial in certain fields that require attention to detail and a high level of responsibility (such as auditing, quality control, and data analysis). However, their tendency towards stress and self-criticism can complicate task execution under pressure and affect their self-esteem. Neurotic employees may perform best in calm and predictable work environments with clear tasks and consistent support. If a company can provide flexible working conditions, clear expectations, and appropriate support, these employees may demonstrate significant attention to detail, diligence, and perseverance. However, they may be more prone to stress and less resilient under pressure, which can sometimes hinder their productivity in situations of high uncertainty or tight deadlines.', 5);

-- --------------------------------------------------------

--
-- Структура таблицы `personality_vacancies`
--

CREATE TABLE IF NOT EXISTS `personality_vacancies` (
  `id` int(11) NOT NULL,
  `personality_id` int(11) NOT NULL,
  `vacancy_id` int(11) NOT NULL,
  `distance` decimal(6,6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Структура таблицы `users`
--

CREATE TABLE IF NOT EXISTS `users` (
  `id` int(11) NOT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `password` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `type` enum('candidate','employer') COLLATE utf8mb4_unicode_ci NOT NULL,
  `info_id` int(11) DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `role` enum('user','admin') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'user'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------

--
-- Структура таблицы `personality_candidates`
--

CREATE TABLE IF NOT EXISTS `personality_candidates` (
  `id` int(11) NOT NULL,
  `personality_id` int(11) NOT NULL,
  `user_id` int(11) NOT NULL,
  `distance` decimal(6,6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------


--
-- Структура таблицы `vacancies`
--

CREATE TABLE IF NOT EXISTS `vacancies` (
  `id` int(11) NOT NULL,
  `name` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` text COLLATE utf8mb4_unicode_ci NOT NULL,
  `salary` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `skill` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `embedding_id` int(11) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Индексы сохранённых таблиц
--

--
-- Индексы таблицы `candidate_vacancies`
--
ALTER TABLE `candidate_vacancies`
  ADD PRIMARY KEY (`id`,`user_id`,`vacancy_id`),
  ADD KEY `user_id` (`user_id`),
  ADD KEY `vacancy_id` (`vacancy_id`);

--
-- Индексы таблицы `embeddings`
--
ALTER TABLE `embeddings`
  ADD PRIMARY KEY (`id`);

--
-- Индексы таблицы `files`
--
ALTER TABLE `files`
  ADD PRIMARY KEY (`id`);

--
-- Индексы таблицы `info_candidates`
--
ALTER TABLE `info_candidates`
  ADD PRIMARY KEY (`id`),
  ADD KEY `file_id` (`file_id`),
  ADD KEY `embedding_id` (`embedding_id`);

--
-- Индексы таблицы `magics`
--
ALTER TABLE `magics`
  ADD PRIMARY KEY (`id`),
  ADD KEY `file_id` (`file_id`);

--
-- Индексы таблицы `personalities`
--
ALTER TABLE `personalities`
  ADD PRIMARY KEY (`id`),
  ADD KEY `embedding_id` (`embedding_id`);

--
-- Индексы таблицы `personality_vacancies`
--
ALTER TABLE `personality_vacancies`
  ADD PRIMARY KEY (`id`,`personality_id`,`vacancy_id`),
  ADD KEY `personality_id` (`personality_id`),
  ADD KEY `vacancy_id` (`vacancy_id`);

--
-- Индексы таблицы `users`
--
ALTER TABLE `users`
  ADD PRIMARY KEY (`id`),
  ADD KEY `info_id` (`info_id`);

--
-- Индексы таблицы `personality_candidates`
--
ALTER TABLE `personality_candidates`
  ADD PRIMARY KEY (`id`,`personality_id`,`user_id`),
  ADD KEY `personality_id` (`personality_id`),
  ADD KEY `user_id` (`user_id`);


--
-- Индексы таблицы `vacancies`
--
ALTER TABLE `vacancies`
  ADD PRIMARY KEY (`id`),
  ADD KEY `embedding_id` (`embedding_id`);

--
-- AUTO_INCREMENT для сохранённых таблиц
--

--
-- AUTO_INCREMENT для таблицы `candidate_vacancies`
--
ALTER TABLE `candidate_vacancies`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT для таблицы `embeddings`
--
ALTER TABLE `embeddings`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT для таблицы `files`
--
ALTER TABLE `files`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT для таблицы `info_candidates`
--
ALTER TABLE `info_candidates`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT для таблицы `magics`
--
ALTER TABLE `magics`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT для таблицы `personalities`
--
ALTER TABLE `personalities`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT для таблицы `personality_vacancies`
--
ALTER TABLE `personality_vacancies`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT для таблицы `users`
--
ALTER TABLE `users`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT для таблицы `personality_candidates`
--
ALTER TABLE `personality_candidates`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;


--
-- AUTO_INCREMENT для таблицы `vacancies`
--
ALTER TABLE `vacancies`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- Ограничения внешнего ключа сохраненных таблиц
--

--
-- Ограничения внешнего ключа таблицы `candidate_vacancies`
--
ALTER TABLE `candidate_vacancies`
  ADD CONSTRAINT `candidate_vacancies_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `candidate_vacancies_ibfk_2` FOREIGN KEY (`vacancy_id`) REFERENCES `vacancies` (`id`) ON DELETE CASCADE;

--
-- Ограничения внешнего ключа таблицы `info_candidates`
--
ALTER TABLE `info_candidates`
  ADD CONSTRAINT `info_candidates_ibfk_1` FOREIGN KEY (`file_id`) REFERENCES `files` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `info_candidates_ibfk_2` FOREIGN KEY (`embedding_id`) REFERENCES `embeddings` (`id`) ON DELETE CASCADE;

--
-- Ограничения внешнего ключа таблицы `magics`
--
ALTER TABLE `magics`
  ADD CONSTRAINT `magics_ibfk_1` FOREIGN KEY (`file_id`) REFERENCES `files` (`id`) ON DELETE CASCADE;

--
-- Ограничения внешнего ключа таблицы `personalities`
--
ALTER TABLE `personalities`
  ADD CONSTRAINT `personalities_ibfk_1` FOREIGN KEY (`embedding_id`) REFERENCES `embeddings` (`id`) ON DELETE CASCADE;

--
-- Ограничения внешнего ключа таблицы `personality_vacancies`
--
ALTER TABLE `personality_vacancies`
  ADD CONSTRAINT `personality_vacancies_ibfk_1` FOREIGN KEY (`personality_id`) REFERENCES `personalities` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `personality_vacancies_ibfk_2` FOREIGN KEY (`vacancy_id`) REFERENCES `vacancies` (`id`) ON DELETE CASCADE;

--
-- Ограничения внешнего ключа таблицы `users`
--
ALTER TABLE `users`
  ADD CONSTRAINT `users_ibfk_1` FOREIGN KEY (`info_id`) REFERENCES `info_candidates` (`id`) ON DELETE CASCADE;

--
-- Ограничения внешнего ключа таблицы `personality_candidates`
--
ALTER TABLE `personality_candidates`
  ADD CONSTRAINT `personality_candidates_ibfk_1` FOREIGN KEY (`personality_id`) REFERENCES `personalities` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `personality_candidates_ibfk_2` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE;


--
-- Ограничения внешнего ключа таблицы `vacancies`
--
ALTER TABLE `vacancies`
  ADD CONSTRAINT `vacancies_ibfk_1` FOREIGN KEY (`embedding_id`) REFERENCES `embeddings` (`id`) ON DELETE CASCADE;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
