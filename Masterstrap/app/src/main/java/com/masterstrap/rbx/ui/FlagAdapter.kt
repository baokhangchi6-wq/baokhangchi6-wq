package com.masterstrap.rbx.ui

import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import com.masterstrap.rbx.data.FlagItem
import com.masterstrap.rbx.data.FlagType
import com.masterstrap.rbx.databinding.ItemFlagRowBinding

class FlagAdapter(
    private var flags: MutableList<FlagItem>,
    private val onDeleteClick: (FlagItem) -> Unit,
    private val onValueChanged: (FlagItem) -> Unit = {}
) : RecyclerView.Adapter<FlagAdapter.FlagViewHolder>() {

    private var filteredFlags: MutableList<FlagItem> = flags.toMutableList()

    inner class FlagViewHolder(val binding: ItemFlagRowBinding) : RecyclerView.ViewHolder(binding.root) {
        var valueWatcher: TextWatcher? = null
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): FlagViewHolder {
        val binding = ItemFlagRowBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return FlagViewHolder(binding)
    }

    override fun onBindViewHolder(holder: FlagViewHolder, position: Int) {
        val item = filteredFlags[position]
        with(holder.binding) {
            // A recycled row still owns the watcher installed for its previous
            // FlagItem. Remove it before setText(), otherwise binding an empty
            // FString can overwrite a different row and the value disappears
            // on the next launch.
            holder.valueWatcher?.let(etFlagValue::removeTextChangedListener)
            tvFlagName.text = item.name
            etFlagValue.setText(item.value)
            etFlagValue.hint = if (item.type == FlagType.STRING && item.value.isEmpty()) {
                "\"\" (empty string)"
            } else {
                null
            }

            holder.valueWatcher = object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                    item.value = s.toString()
                    onValueChanged(item)
                }
                override fun afterTextChanged(s: Editable?) {}
            }
            etFlagValue.addTextChangedListener(holder.valueWatcher)

            btnDeleteFlag.setOnClickListener {
                onDeleteClick(item)
            }
        }
    }

    override fun getItemCount(): Int = filteredFlags.size

    override fun onViewRecycled(holder: FlagViewHolder) {
        holder.valueWatcher?.let(holder.binding.etFlagValue::removeTextChangedListener)
        holder.valueWatcher = null
        super.onViewRecycled(holder)
    }

    fun filter(query: String) {
        filteredFlags = if (query.isEmpty()) {
            flags.toMutableList()
        } else {
            flags.filter { it.name.contains(query, ignoreCase = true) }.toMutableList()
        }
        notifyDataSetChanged()
    }

    fun addFlag(flag: FlagItem) {
        flags.add(0, flag)
        filteredFlags.add(0, flag)
        notifyItemInserted(0)
    }

    fun removeFlag(flag: FlagItem) {
        val idx = flags.indexOf(flag)
        if (idx != -1) {
            flags.removeAt(idx)
            val filteredIdx = filteredFlags.indexOf(flag)
            if (filteredIdx != -1) {
                filteredFlags.removeAt(filteredIdx)
                notifyItemRemoved(filteredIdx)
            }
        }
    }

    fun getAllFlags(): List<FlagItem> = flags
}
