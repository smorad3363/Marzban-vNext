import { CreateToastFnReturn } from "@chakra-ui/react";
import { UseFormReturn } from "react-hook-form";
import { localizedApiError, safeUserMessage } from "./apiError";

export const generateErrorMessage = (
  e: any,
  toast: CreateToastFnReturn,
  form?: UseFormReturn<any>
) => {
  if (e.response && e.response._data) {
    const detail = e.response._data.detail;
    if (form && detail && typeof detail === "object" && detail.fields) {
      Object.entries(detail.fields).forEach(([field, message]) =>
        form.setError(field, { message: safeUserMessage(message) || localizedApiError(e) })
      );
      return;
    }
  }
  return toast({
    title: localizedApiError(e),
    status: "error",
    isClosable: true,
    position: "top",
    duration: 3000,
  });
};

export const generateSuccessMessage = (
  message: string,
  toast: CreateToastFnReturn
) => {
  return toast({
    title: message,
    status: "success",
    isClosable: true,
    position: "top",
    duration: 3000,
  });
};
