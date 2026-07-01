import type { DocumentRecipeView } from "@/lib/api";

export function resolveSelectedRecipe(
  recipes: DocumentRecipeView[],
  requestedRecipeId: string | null
) {
  return recipes.find((recipe) => recipe.recipe_id === requestedRecipeId) ?? recipes[0] ?? null;
}

export function recipeIsActive(recipe: DocumentRecipeView) {
  return recipe.steps.some((step) => step.status === "QUEUED" || step.status === "RUNNING");
}

const EDITABLE_STATUSES: DocumentRecipeView["status"][] = ["UPLOADED", "INDEXED", "ERROR"];

/** 処理中・確認待ちのレシピは処理設定編集を禁止する(実行中ジョブに限らない)。 */
export function recipeConfigLocked(recipe: DocumentRecipeView) {
  return recipeIsActive(recipe) || !EDITABLE_STATUSES.includes(recipe.status);
}

export function canAddRecipe(recipeCount: number) {
  return recipeCount < 3;
}

export function canDeleteRecipe(recipeCount: number, active: boolean) {
  return recipeCount > 1 && !active;
}

