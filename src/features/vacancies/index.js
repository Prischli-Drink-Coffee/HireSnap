// The vacancy slice: everything that renders or creates a vacancy.
//
// A barrel is the feature's public edge. Pages import from here, never from
// components/, so a component can move inside the feature without touching a
// single page - and so the import list states plainly what the feature offers.
export { default as TableVacancies } from "features/vacancies/components/TableVacancies";
export { default as VacancyCreateForm } from "features/vacancies/components/VacancyCreateForm";
