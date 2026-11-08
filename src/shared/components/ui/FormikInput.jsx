import React from "react";
import { FormControl, FormErrorMessage, FormLabel, Input } from "@chakra-ui/react";

// One labelled input bound to a formik instance. Every form in the app uses it,
// so the error state and the 40px height stay identical everywhere.
const FormikInput = ({ label, formik, name, type, change }) => {
  const hasError = Boolean(formik.errors[name] && formik.touched[name]);

  return (
    <FormControl isInvalid={hasError}>
      <FormLabel>{label}</FormLabel>
      <Input
        errorBorderColor="crimson"
        id={name}
        name={name}
        value={formik.values[name]}
        onChange={change ? change : formik.handleChange}
        height="40px"
        type={type}
        placeholder={label}
      />
      {hasError && <FormErrorMessage>{formik.errors[name]}</FormErrorMessage>}
    </FormControl>
  );
};

export default FormikInput;
