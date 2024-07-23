from typing import Any, Callable, Optional, Union

from rl4co.envs.common.base import RL4COEnvBase
from rl4co.models.rl import REINFORCE
from rl4co.models.rl.reinforce.baselines import REINFORCEBaseline
from rl4co.models.zoo.glop.policy import GLOPPolicy
from rl4co.utils.ops import gather_by_index, unbatchify

GLOP_SUPPORTED_ENVS = {"cvrp", "cvrpmvc"}


class GLOP(REINFORCE):
    """Global and Local Optimization Policies (GLOP) REINFORCE: https://arxiv.org/abs/2312.08224

    Args:
        env: Environment to use for the algorithm
        policy: Policy to use for the algorithm
        baseline: REINFORCE baseline. Defaults to rollout (1 epoch of exponential, then greedy rollout baseline)
        revisers: List of revisers to use for the GLOP revision phase, the reviser could be a neural network model
            or a heuristic function. Defaults to None, but this is required.
        n_samples: Number of samples to use for the GLOP policy. Defaults to 10.
        policy_kwargs: Keyword arguments for policy
        baseline_kwargs: Keyword arguments for baseline
        **kwargs: Keyword arguments passed to the superclass
    """

    def __init__(
        self,
        env: RL4COEnvBase,
        policy: Optional[GLOPPolicy] = None,
        baseline: Union[REINFORCEBaseline, str] = "shared",
        revisers: list[Callable] = None,
        policy_kwargs={},
        baseline_kwargs={},
        **kwargs,
    ):
        # TODO: test more VRPs
        assert (
            env.name in GLOP_SUPPORTED_ENVS
        ), f"{env.name} is not supported by {self.__class__.__name__}"

        if policy is None:
            policy = GLOPPolicy(env_name=env.name, **policy_kwargs)

        super().__init__(env, policy, baseline, baseline_kwargs, **kwargs)

    def shared_step(
        self, batch: Any, batch_idx: int, phase: str, dataloader_idx: Optional[int] = None
    ):
        td = self.env.reset(batch)
        n_samples = self.policy.n_samples

        # Evaluate policy
        out = self.policy(
            td=td,
            env=self.env,
            phase=phase,
            return_actions=True,
        )
        reward = unbatchify(out["reward"], n_samples)
        max_reward, max_idxs = reward.max(dim=-1)
        out.update({"max_reward": max_reward})

        # Training phase
        if phase == "train":
            assert n_samples > 1, "num_starts must be > 1 during training"
            log_likelihood = unbatchify(out["log_likelihood"], n_samples)
            self.calculate_loss(td, batch, out, reward, log_likelihood)

        # Get multi-start (=POMO) rewards and best actions only during validation and test
        else:
            if n_samples > 1:
                if out.get("actions", None) is not None:
                    # Reshape batch to [batch_size, num_augment, num_starts, ...]
                    actions = unbatchify(out["actions"], (n_samples))
                    out.update(
                        {
                            "best_multistart_actions": gather_by_index(
                                actions, max_idxs, dim=max_idxs.dim()
                            )
                        }
                    )
                    out["actions"] = actions

        metrics = self.log_metrics(out, phase, dataloader_idx=dataloader_idx)
        return {"loss": out.get("loss", None), **metrics}
