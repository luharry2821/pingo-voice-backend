import { handleSttToken, preflight } from "../../server/handlers.js";
export const POST = (request: Request) => handleSttToken(request);
export const OPTIONS = () => preflight();
