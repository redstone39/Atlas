export interface PasswordConfirmationState {
  tooShort: boolean;
  mismatch: boolean;
  valid: boolean;
}

export function passwordConfirmationState(
  password: string,
  confirmation: string,
): PasswordConfirmationState {
  const tooShort = password.length > 0 && password.length < 12;
  const mismatch =
    password.length >= 12 &&
    confirmation.length > 0 &&
    password !== confirmation;
  return {
    tooShort,
    mismatch,
    valid: password.length >= 12 && password === confirmation,
  };
}
