import type { ColumnOf } from "../withColumns";
import type { judgmentMessages } from "../judgmentMessages";

export const judgmentJa: ColumnOf<typeof judgmentMessages> = {
  "testOrder.note":
    "順序はスコアではなくルールに従います。原因の候補が最も少なくなるテストが先頭で、次にリスクが低いもの、コストが低いもの、元に戻せるもの、作成順の順に並びます。不明な予測は、どの結果でも成り立ちうるものとして残ります。もっともらしさは順序に使いません。根拠は仮説カードに別に表示されます。テスト結果が出ると、順序は変わることがあります。",
  "testOrder.excluded": "順序から除外された行動（禁止されたリスク）",
  "testOrder.uncountable":
    "ほかの仮説の予測がないため、候補を1つしか減らせないものとして数えています。",
  "testOrder.noReduction": "このテストだけでは、考えられる原因を絞り込めません。",
  "judgment.this": "この仮説に合う",
  "judgment.alternative": "別の説明に合う",
  "judgment.neither": "どちらにも合わない",
  "judgment.undetermined": "未確定",
  "judgment.recorded": "記録済み",
  "judgment.excludedOnce": "除外（記録された結果が1件）",
  "judgment.excludedRepeated": "除外（結果が繰り返し得られた）",
  "judgment.eliminationNote":
    "記録されたテスト結果は、別の説明に合っています。仮説は残され、結果を記録し直すとこの表示は変わることがあります。",
  "judgment.againstOnce": "別の説明に合う（結果が1件）",
  "judgment.againstRepeated": "別の説明に合う（繰り返し）",
  "judgment.mixed": "結果が分かれている",
  "judgment.standingNote":
    "これは人が記録したテスト結果をまとめたものです。封印されたテストが確立した検証状態は変わりません。",
  "judgment.againstNote":
    "この行動を支える仮説の記録済み結果は、別の説明に合っています。この通知は承認や実行を止めるものではありません。判断する前にテスト結果を確認してください。",
  "judgment.fixApplied": "修正を適用済み（効果は未検証）",
  "judgment.fixHint":
    "修正は適用されましたが、その効果は新しい結果で検証されていません。新しい結果によってこの行が基準を満たすと、ルールが効果を確認済みとして記録します。",
  "judgment.humanClosed": "人が完了を確認（テスト結果なし）",
  "judgment.humanHint": "テスト結果なしで、人が完了を確認しました。",
  "judgment.waiver": "逸脱または免除を記録（元の基準は未達のまま）",
  "judgment.waiverHint":
    "システムの外で承認された逸脱または免除を記録します。元の基準は未達のままです。",
  "judgment.conditionChanged": "運用条件が変更された",
  "judgment.conditionHint":
    "基準が適用される運用条件の変更を記録します。条件の範囲を指定する必要があります。",
  "judgment.closureNote":
    "システムの外で人が下した処置を記録します。THOTH は免除や承認を与えません。ルールで計算された判定は変わりません。",
  "judgment.effectConfirmed": "効果を確認済み",
  "judgment.unspecified": "未指定",
  "judgment.closureRecorded": "処置を記録済み",
  "judgment.effectNotice":
    "修正の記録後に得られた新しい結果によって、この行がルールの計算上、基準を満たしました。",
  "judgment.externalWaiver":
    "これはシステムの外で下された処置の記録です。THOTH が免除を与えたわけではありません。",
  "judgment.verdictChanged":
    "この記録のあとで判定が変わりました。この記録は、当時の判定を指します。",
};
