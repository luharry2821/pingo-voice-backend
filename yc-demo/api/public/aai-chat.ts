import { handleChat, preflight } from "../../server/handlers.js";
export const POST = (request: Request) => handleChat(request);
export const OPTIONS = () => preflight();
